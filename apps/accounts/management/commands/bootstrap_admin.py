"""Create the first HAL Administrator on a fresh production database.

    python manage.py bootstrap_admin --username admin --email admin@hal.example

The password is read from HAL_ADMIN_PASSWORD or prompted, never passed on the command line.
"""
import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User


class Command(BaseCommand):
    help = "Create the first HAL Administrator account."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    def handle(self, *args, **opts):
        if User.objects.filter(role=Role.ADMIN).exists():
            raise CommandError("An administrator already exists. Use the Users screen to manage accounts.")
        password = os.environ.get("HAL_ADMIN_PASSWORD") or getpass.getpass("Password: ")
        user = User(
            username=opts["username"],
            email=opts["email"],
            first_name=opts["first_name"],
            last_name=opts["last_name"],
            role=Role.ADMIN,
            must_change_password=True,
        )
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Administrator '{user.username}' created. The password must be changed at first sign-in."))
