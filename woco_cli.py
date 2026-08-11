"""Console entry point for the 'woco' command.

Exposes Django's manage.py as a top-level CLI: 'woco runserver' is
equivalent to 'python backend/manage.py runserver' with backend/ on
sys.path. All built-in and custom Django management commands work
identically (same args, same exit codes). The ASCC workflow also has a
grouped public wrapper at './woco ascc', including './woco ascc import'
for import_ascc_bundle and './woco ascc drop' for drop_ascc_state. The
data-deploy scripts are exposed as './woco push' and './woco reload'.
'./woco secretkey' prints a fresh DJANGO_SECRET_KEY value; it runs before
Django settings load, so it works on a fresh clone that has no .env yet.
'./woco setup dev' and './woco setup prod' run the one-command environment
setup in tools/setup.sh.
"""
import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) > 1 and sys.argv[1] == "ascc":
        sys.path.insert(0, os.path.join(here, "tools"))
        from ascc_cli import main as ascc_main
        raise SystemExit(ascc_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "push":
        script = os.path.join(here, "tools", "push_data.sh")
        os.execv(script, [script] + sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "reload":
        script = os.path.join(here, "tools", "reload_data.sh")
        os.execv(script, [script] + sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        script = os.path.join(here, "tools", "setup.sh")
        os.execv(script, [script] + sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "secretkey":
        # Deliberately does not load Django settings: settings require
        # DJANGO_SECRET_KEY, which is exactly what a fresh clone lacks.
        from django.core.management.utils import get_random_secret_key
        print(get_random_secret_key())
        raise SystemExit(0)
    sys.path.insert(0, os.path.join(here, "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "woco.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(["woco"] + sys.argv[1:])


if __name__ == "__main__":
    main()
