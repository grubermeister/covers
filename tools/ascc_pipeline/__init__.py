"""Internal helpers for the public ./woco ascc command.

The public command surface lives in tools/ascc_cli.py. This package owns the
shared ASCC path, default, command, check, and manifest helpers so lower-level
stage scripts do not need hidden state-specific defaults.
"""

