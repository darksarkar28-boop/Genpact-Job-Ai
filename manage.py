"""Django management entry point for the hosted profile builder."""
import os
import sys

from django.core.management import execute_from_command_line


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")
    execute_from_command_line(sys.argv)
