"""Small TOML templates returned to clients.

If a repo (or readme.toml) doesn't exist yet, the frontend can start from a minimal
template and then POST the edited TOML back to the server.
"""

from __future__ import annotations


def normal_template(*, course_name: str, course_code: str) -> str:
    return (
        f"course_name = \"{course_name}\"\n"
        f"repo_type = \"normal\"\n"
        f"course_code = \"{course_code}\"\n\n"
        'description = ""\n\n'
        "# final schema: unified [[sections]] (optional; missing treated as empty)\n"
    )


def multiproject_template(*, course_name: str, course_code: str) -> str:
    # Minimal stub; final schema uses [[courses]] with {name, code}.
    return (
        f"course_name = \"{course_name}\"\n"
        f"repo_type = \"multi-project\"\n"
        f"course_code = \"{course_code}\"\n\n"
        'description = ""\n\n'
        "[[courses]]\n"
        "name = \"子课程\"\n"
        "code = \"\"\n"
    )
