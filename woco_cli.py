"""Console entry point for the 'woco' command.

Exposes Django's manage.py as a top-level CLI: 'woco runserver' is
equivalent to 'python backend/manage.py runserver' with backend/ on
sys.path. All built-in and custom Django management commands work
identically (same args, same exit codes), including the project's
custom imports (import_ascc_bundle, import_catalog_images,
import_apmc_bundle, ...).
"""
import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "woco.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(["woco"] + sys.argv[1:])


if __name__ == "__main__":
    main()
