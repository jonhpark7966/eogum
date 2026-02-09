import logging

import resend

from eogum.config import settings

logger = logging.getLogger(__name__)


def _send(to: str, subject: str, html: str) -> None:
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set, skipping email to %s: %s", to, subject)
        return

    resend.api_key = settings.resend_api_key
    resend.Emails.send({
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "html": html,
    })


def send_completion_email(to: str, project_name: str, project_id: str, cut_percentage: float) -> None:
    subject = f"[어검] \"{project_name}\" 편집이 완료되었습니다"
    html = f"""
    <h2>편집이 완료되었습니다!</h2>
    <p><strong>프로젝트:</strong> {project_name}</p>
    <p><strong>컷 비율:</strong> {cut_percentage:.1f}%</p>
    <p><a href="https://eogum.sudoremove.com/projects/{project_id}">결과 확인하기</a></p>
    """
    _send(to, subject, html)


def send_failure_email(to: str, project_name: str, project_id: str, error: str) -> None:
    subject = f"[어검] \"{project_name}\" 처리 중 오류가 발생했습니다"
    html = f"""
    <h2>처리 중 오류가 발생했습니다</h2>
    <p><strong>프로젝트:</strong> {project_name}</p>
    <p><strong>오류:</strong> {error}</p>
    <p>홀딩된 크레딧은 자동으로 복구되었습니다.</p>
    <p><a href="https://eogum.sudoremove.com/projects/{project_id}">프로젝트 확인하기</a></p>
    """
    _send(to, subject, html)
