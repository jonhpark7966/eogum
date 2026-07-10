from eogum.config import settings


_BUILTIN_PUBLIC_PROJECT_IDS = {
    "3d2587aa-f65a-4746-a454-30bba7611ddc",
    "b094cf1c-bf9b-49f1-8a45-c646e3734692",
    "296ec362-a5eb-405d-a839-3f65509a3ace",
}


def _csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def public_project_ids() -> set[str]:
    return _BUILTIN_PUBLIC_PROJECT_IDS | _csv_values(settings.public_project_ids)


def is_public_project_id(project_id: str) -> bool:
    return project_id in public_project_ids()
