import json
import logging
import time

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_GATEWAY_ATTEMPTS = 3


class AiGateway(models.AbstractModel):
    _name = 'os.ai.gateway'
    _description = 'AI Gateway'

    # Public API
    @api.model
    def execute(self, prompt, feature_type=None, module=None, **kwargs):
        """Single entry point for all AI requests.

        Args:
            prompt (str): The request prompt. Required.
            feature_type (str, optional): e.g. 'crm.lead_enrichment' or 'summarize'.
                If omitted, auto-detected from prompt keywords.
            module (str, optional): Owning module e.g. 'crm'. Narrows lookup when given.
            **kwargs:
                system_prompt (str): Override feature default system instructions.
                response_format (dict): JSON Schema / JSON mode response format.
                images (list): Base64 image strings for vision tasks.
                state (dict|str): Context data for decision models.
                questions (dict): Questions for decision models.
                context_data (dict): Arbitrary metadata (logged, not sent to LLM).

        Returns:
            dict: Normalized gateway response dictionary.
        """
        t0 = time.time()

        self._validate_request(prompt, kwargs)
        feature, detected = self._resolve_feature(prompt, feature_type, module)
        complexity, score = self._evaluate_complexity(feature, prompt, kwargs)
        candidates = self._select_candidates(feature, complexity)

        if not candidates:
            raise UserError(
                _("No active AI provider found supporting capability '%s' for feature '%s'.")
                % (feature.required_capability, feature.feature_type)
            )

        attempts, final_result = self._execute_with_fallback(feature, candidates, prompt, kwargs)
        gateway_duration = time.time() - t0

        self._log_request(feature, complexity, score, attempts, final_result, kwargs, prompt, gateway_duration)
        return self._build_response(feature, detected, complexity, score, attempts, final_result)

    # Pipeline Methods
    @api.model
    def _validate_request(self, prompt, kwargs):
        """Validate input types and required parameters."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise UserError(_("Prompt must be a non-empty string."))

        images = kwargs.get('images')
        if images is not None and not isinstance(images, (list, tuple)):
            raise UserError(_("Images must be a list or tuple of base64 strings."))

        response_format = kwargs.get('response_format')
        if response_format is not None and not isinstance(response_format, dict):
            raise UserError(_("response_format must be a dictionary."))

        questions = kwargs.get('questions')
        if questions is not None and not isinstance(questions, dict):
            raise UserError(_("questions must be a dictionary."))

    @api.model
    def _resolve_feature(self, prompt, feature_type=None, module=None):
        """Resolve feature by explicit feature_type or auto-detect via keywords.

        Returns:
            tuple: (os.ai.feature record, bool was_auto_detected)
        """
        Feature = self.env['os.ai.feature']

        if feature_type:
            domain = [
                ('feature_type', '=', feature_type),
                ('active', '=', True),
                ('is_deleted', '=', False),
            ]
            if module:
                domain.append(('module', '=', module))

            feature = Feature.search(domain, limit=1)
            if not feature:
                msg = _("AI Feature '%s' not found or inactive.") % feature_type
                if module:
                    msg = _("AI Feature '%s' for module '%s' not found or inactive.") % (feature_type, module)
                raise UserError(msg)
            return feature, False

        # Auto-detection by keyword matching
        active_features = Feature.search([
            ('active', '=', True),
            ('is_deleted', '=', False),
            ('feature_keywords', '!=', False),
        ], order='sequence asc, id asc')

        prompt_lower = prompt.lower()
        best_feature = Feature.browse()
        max_matches = 0

        for feat in active_features:
            keywords = [k.strip().lower() for k in feat.feature_keywords.split(',') if k.strip()]
            matches = sum(1 for kw in keywords if kw in prompt_lower)
            if matches > max_matches:
                max_matches = matches
                best_feature = feat

        if not best_feature or max_matches == 0:
            raise UserError(
                _("No AI feature matched your request. Please specify feature_type explicitly.")
            )

        return best_feature, True

    @api.model
    def _evaluate_complexity(self, feature, prompt, kwargs):
        """Evaluate complexity using a weighted scoring engine.

        Returns:
            tuple: (str tier ['low', 'medium', 'high'], int raw_score)
        """
        points = 0

        # Base Points from feature.default_complexity
        base_points = {'low': 1, 'medium': 3, 'high': 5}
        points += base_points.get(feature.default_complexity, 1)

        # Complexity Keyword Matches
        if feature.complexity_keywords:
            keywords = [k.strip().lower() for k in feature.complexity_keywords.split(',') if k.strip()]
            prompt_lower = prompt.lower()
            matches = sum(1 for kw in keywords if kw in prompt_lower)
            points += matches * 2

        # Prompt Characteristics
        # Length
        prompt_len = len(prompt)
        if prompt_len > 8000:
            points += 3
        elif prompt_len > 4000:
            points += 2
        elif prompt_len > 1000:
            points += 1

        # Images attached
        images = kwargs.get('images') or []
        if len(images) >= 2:
            points += 2
        elif len(images) == 1:
            points += 1

        # Structured output schema size
        response_format = kwargs.get('response_format') or {}
        schema = response_format.get('json_schema', {}).get('schema', {})
        prop_count = len(schema.get('properties', {}))
        if prop_count > 15:
            points += 2
        elif prop_count > 5:
            points += 1

        # Decision questions count
        questions = kwargs.get('questions') or {}
        if len(questions) >= 4:
            points += 2
        elif len(questions) >= 2:
            points += 1

        # 4. Map to tier
        if points >= 7:
            complexity = 'high'
        elif points >= 4:
            complexity = 'medium'
        else:
            complexity = 'low'

        return complexity, points

    @api.model
    def _select_candidates(self, feature, complexity):
        """Select eligible providers matching required capability and complexity.

        Tier-specific providers are checked first; universal providers (no tier set)
        are appended as fallbacks.
        """
        Provider = self.env['os.ai.provider']
        capability = feature.required_capability

        # Step 1: Providers specifically configured for this complexity tier
        tier_providers = Provider.search([
            ('active', '=', True),
            (capability, '=', True),
            ('complexity_tier', '=', complexity),
        ], order='sequence asc, id asc')

        # Step 2: Universal providers (complexity_tier unset)
        universal_providers = Provider.search([
            ('active', '=', True),
            (capability, '=', True),
            ('complexity_tier', '=', False),
        ], order='sequence asc, id asc')

        candidates = (tier_providers | universal_providers)[:MAX_GATEWAY_ATTEMPTS]
        return candidates

    @api.model
    def _execute_with_fallback(self, feature, candidates, prompt, kwargs):
        """Execute against candidates in priority order with automatic fallback on retryable failures."""
        attempts = []
        final_result = {}

        system_prompt = kwargs.get('system_prompt') or feature.system_prompt or ''
        response_format = kwargs.get('response_format')
        images = kwargs.get('images')
        state = kwargs.get('state')
        questions = kwargs.get('questions')

        for i, provider in enumerate(candidates):
            is_fallback = (i > 0)
            fallback_reason = attempts[-1]['error'] if is_fallback else False

            if feature.operation == 'decision':
                result = provider.call_decision(state=state, questions=questions)
            else:
                result = provider.call_llm(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    response_format=response_format,
                    images=images,
                    capability_used=feature.required_capability,
                )

            attempt = {
                'attempt_number': i + 1,
                'provider_id': provider.id,
                'provider_model': result.get('model') or provider._build_litellm_model(),
                'is_fallback': is_fallback,
                'fallback_reason': fallback_reason,
                'success': result.get('success', False),
                'error': result.get('error'),
                'error_kind': result.get('error_kind'),
                'duration': result.get('duration', 0.0),
                'prompt_tokens': result.get('prompt_tokens', 0),
                'completion_tokens': result.get('completion_tokens', 0),
                'total_tokens': result.get('total_tokens', 0),
                'cost': result.get('cost', 0.0),
                'cost_status': result.get('cost_status', 'unknown'),
                'content': result.get('content', ''),
                'raw_response': result.get('raw_response', {}),
                'finish_reason': result.get('finish_reason'),
                'answers': result.get('answers'),
            }
            attempts.append(attempt)
            final_result = result

            if result.get('success'):
                break

            # Permanent failure: abort immediately (auth, bad schema, refused content)
            if result.get('error_kind') == 'permanent':
                _logger.warning(
                    "os_ai.gateway: provider '%s' failed permanently: %s. Aborting fallback.",
                    provider.name, result.get('error'),
                )
                break

            _logger.info(
                "os_ai.gateway: provider '%s' failed (%s). Attempting fallback if available.",
                provider.name, result.get('error'),
            )

        return attempts, final_result

    @api.model
    def _log_request(self, feature, complexity, score, attempts, final_result, kwargs, prompt, gateway_duration):
        """Record an os.ai.log entry for each execution attempt."""
        AiLog = self.env['os.ai.log'].sudo()

        context_data = kwargs.get('context_data')
        request_context = json.dumps(context_data) if context_data is not None else False
        system_prompt = kwargs.get('system_prompt') or feature.system_prompt or ''

        for attempt in attempts:
            vals = {
                'feature_id': feature.id,
                'feature_type': feature.feature_type,
                'complexity': complexity,
                'complexity_score': score,
                'is_fallback': attempt['is_fallback'],
                'fallback_reason': attempt['fallback_reason'],
                'attempt_number': attempt['attempt_number'],
                'request_uid': self.env.uid,
                'request_context': request_context,
                'provider_id': attempt['provider_id'],
                'provider_model': attempt['provider_model'],
                'system_prompt': system_prompt,
                'user_prompt': prompt,
                'response_content': attempt['content'] if attempt['success'] else '',
                'response_raw': json.dumps(attempt.get('raw_response', {})) if attempt.get('raw_response') else False,
                'success': attempt['success'],
                'error_message': attempt['error'],
                'prompt_tokens': attempt['prompt_tokens'],
                'completion_tokens': attempt['completion_tokens'],
                'total_tokens': attempt['total_tokens'],
                'response_cost': attempt['cost'],
                'cost_status': attempt['cost_status'],
                'duration': attempt['duration'],
            }
            AiLog.log_call(vals)

    @api.model
    def _build_response(self, feature, detected, complexity, score, attempts, final_result):
        """Build the standardized response dictionary."""
        last_attempt = attempts[-1] if attempts else {}
        return {
            'success': final_result.get('success', False),
            'content': final_result.get('content', ''),
            'error': final_result.get('error'),
            'error_kind': final_result.get('error_kind'),
            'feature_type': feature.feature_type,
            'module': feature.module,
            'feature_detected': detected,
            'complexity': complexity,
            'complexity_score': score,
            'provider_id': last_attempt.get('provider_id'),
            'model': last_attempt.get('provider_model'),
            'attempts': len(attempts),
            'is_fallback': last_attempt.get('is_fallback', False),
            'fallback_reason': last_attempt.get('fallback_reason'),
            'prompt_tokens': final_result.get('prompt_tokens', 0),
            'completion_tokens': final_result.get('completion_tokens', 0),
            'total_tokens': final_result.get('total_tokens', 0),
            'cost': final_result.get('cost', 0.0),
            'cost_status': final_result.get('cost_status', 'unknown'),
            'duration': final_result.get('duration', 0.0),
            'answers': final_result.get('answers'),
            'finish_reason': final_result.get('finish_reason'),
        }

