from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AiFeature(models.Model):
    _name = 'os.ai.feature'
    _description = 'AI Feature'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True)
    feature_type = fields.Char(
        'Feature Type',
        required=True,
        index=True,
        help="Unique technical identifier, e.g. 'crm.lead_enrichment' or 'summarize'.",
    )
    module = fields.Char(
        'Module',
        required=True,
        help="Owning Odoo module technical name, e.g. 'crm'.",
    )
    description = fields.Text('Description', help="Purpose and documentation of this feature.")

    operation = fields.Selection([
        ('llm', 'LLM Completion'),
        ('decision', 'Decision Model'),
    ], string='Operation', default='llm', required=True,
        help="Determines whether the gateway executes via call_llm() or call_decision().",
    )
    required_capability = fields.Selection([
        ('capable_text', 'Text Generation'),
        ('capable_vision', 'Vision (Image to Text)'),
        ('capable_image_generation', 'Image Generation'),
        ('capable_image_edit', 'Image Editing'),
        ('capable_decision', 'Typed Decisions'),
    ], string='Required Capability', default='capable_text', required=True,
        help="The provider capability required by this feature.",
    )
    system_prompt = fields.Text(
        'Default System Prompt',
        help="Default system instructions for this feature. Can be overridden in execute().",
    )

    default_complexity = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ], string='Default Complexity', default='low', required=True,
        help="Baseline complexity tier before prompt characteristics or keywords are evaluated.",
    )

    feature_keywords = fields.Text(
        'Feature Detection Keywords',
        help="Comma-separated keywords used to identify this feature from user prompts. "
             "e.g. 'summarize, summary, tldr, recap, brief'",
    )

    complexity_keywords = fields.Text(
        'High Complexity Keywords',
        help="Comma-separated keywords that escalate requests to High complexity. "
             "e.g. 'exhaustive, comprehensive, line-by-line, audit'",
    )

    active = fields.Boolean('Active', default=True)
    is_deleted = fields.Boolean(
        'Is Deleted',
        default=False,
        help="Soft-deleted features are hidden but kept for audit logs.",
    )
    deleted_at = fields.Datetime('Deleted At', readonly=True)
    sequence = fields.Integer('Sequence', default=10)

    _sql_constraints = [
        ('module_feature_type_uniq', 'unique(module, feature_type)', 'The combination of Module and Feature Type must be unique.'),
    ]

    # Developer API Methods 

    @api.model
    def register_feature(self, vals):
        """Create or update a feature by its module and technical feature_type.

        Idempotent: if a feature with the given (module, feature_type) already exists,
        its fields are updated.
        """
        if not vals.get('module') or not vals.get('feature_type'):
            raise UserError("Both Module and Feature Type are required to register a feature.")

        existing = self.search([
            ('module', '=', vals['module']),
            ('feature_type', '=', vals['feature_type']),
        ], limit=1)

        if existing:
            existing.write(vals)
            return existing
        return self.create(vals)

    def deactivate_feature(self):
        """Deactivate feature to stop routing while preserving configuration."""
        return self.write({'active': False})

    def delete_feature(self):
        """Soft-delete feature so historical audit logs remain valid."""
        return self.write({
            'active': False,
            'is_deleted': True,
            'deleted_at': fields.Datetime.now(),
        })

    def restore_feature(self):
        """Restore an inactive or soft-deleted feature back to active."""
        return self.write({
            'active': True,
            'is_deleted': False,
            'deleted_at': False,
        })
