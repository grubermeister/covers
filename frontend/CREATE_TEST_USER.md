# Create Test User for WorldCovers

## Test User Credentials (example)

**Email:** `admin@worldcovers.test`
**Password:** `TestAdmin123!`

## Recommended: Django admin

1. From the project root:
   ```bash
   woco createsuperuser
   ```
2. Use the credentials above when prompted.
3. Log in at:
   - Local: `http://127.0.0.1:8000/admin/`
   - Staging: `https://hellowoco.app/admin/`

## Optional: Create a regular user via Django shell

From the project root, with the default local database configured, run:

```bash
woco shell
```

```python
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.create_user(email="admin@worldcovers.test", password="TestAdmin123!")
```

## After Creating the User

1. Visit the login page (e.g., `/auth`).
2. Sign in with the credentials above.

## Security Note

Use strong, unique passwords for any non-test environment.
