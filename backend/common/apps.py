###################################################################################################
## WoCo Commons - Application Settings
## MPC: 2025/10/24
###################################################################################################
from django.apps import AppConfig
from django.apps import apps
import reversion


# Append-only audit/snapshot tables maintained by common/audit.py. Registering
# them with reversion would double-store every custom snapshot (history of
# history), so they are excluded from version tracking.
REVERSION_EXCLUDED_MODELS = frozenset({
    "submissiontransaction",
    "markingversion",
    "coverversion",
    "markingrecyclebin",
    "coverrecyclebin",
})


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "common"
    verbose_name = "Commons"

    def ready(self):
        # Auto-register all non-abstract models in this app with django-reversion
        app_config = apps.get_app_config("common")

        for model in app_config.get_models():
            if model._meta.abstract:
                continue
            if model._meta.model_name in REVERSION_EXCLUDED_MODELS:
                continue
            if not reversion.is_registered(model):
                reversion.register(model)

        # Register signal handlers (e.g. send email when user is set Active in admin)
        from . import signals  # noqa: F401

###################################################################################################
