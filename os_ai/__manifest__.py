# -*- coding: utf-8 -*-
# Copyright 2026 Alain Bloos <alainbloos@gmail.com>
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    'name': 'OS AI Base',
    'version': '19.0.1.2.0',
    'category': 'Technical',
    'summary': 'AI gateway, provider management, capability routing, and logging via litellm',
    'description': """
        AI Gateway and base module to configure AI Providers (OpenAI, Gemini, Anthropic, etc),
        track logs (prompts, responses, tokens, cost), manage reusable prompt templates,
        and provide a unified AI Gateway with intelligent routing and automatic fallback.

        Requires the `litellm` Python package, version 1.104.3 or newer.
    """,
    'author': 'Alain Bloos',
    'website': 'https://github.com/alainbloos/odoo_os_ai',
    'depends': ['base'],
    'external_dependencies': {
        # 1.104.3: first release with litellm.systemone() (decision models). Older ones
        # also drop the reasoning effort of GPT-6 / Claude 5.x or fail on Python 3.10
        'python': ['litellm>=1.104.3'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/ai_provider_views.xml',
        'views/ai_feature_views.xml',
        'views/ai_prompt_views.xml',
        'views/ai_log_views.xml',
        'views/ai_dashboard.xml',
    ],
    'installable': True,
    'application': True,
    'images': ['static/description/icon.png'],
    'license': 'LGPL-3',
}
