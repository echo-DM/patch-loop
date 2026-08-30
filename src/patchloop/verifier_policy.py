from __future__ import annotations


ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")


def is_sensitive_env_name(name: str) -> bool:
    return name == ".env" or (
        name.startswith(".env.") and not name.endswith(ENV_TEMPLATE_SUFFIXES)
    )
