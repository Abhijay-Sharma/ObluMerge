"""
recruitment/automation.py

The PM's ask: "when a candidate moves stage, someone should hear about it,
without HR typing an email every time." This is the single hook that runs
on every stage move (called from ApplicationMoveStageView).

Kept synchronous and simple on purpose. If volume grows, swap the body of
each branch for a `.delay(...)` call to a Celery task — the call site in
views.py doesn't need to change.
"""
from django.core.mail import send_mail
from django.conf import settings


def on_stage_changed(application, old_stage, new_stage):
    stage_name = new_stage.name.lower()

    if 'technical' in stage_name:
        _send(
            application,
            subject=f'Next step: {new_stage.name}',
            body=(f'Hi {application.candidate.first_name},\n\n'
                  f'You have moved to the "{new_stage.name}" round for {application.job_posting.title}. '
                  f'We will share the schedule/location shortly.\n\nRegards,\n{application.job_posting.company}'),
        )

    elif stage_name in ('rejected', 'reject'):
        # Spec asks for a delay "to look natural" — do that with a scheduled
        # task (Celery/cron) rather than blocking the request thread:
        #   send_rejection_email.apply_async(args=[application.pk], countdown=60 * 60 * 24)
        # Left as a comment since it needs your task runner wired up; calling
        # _send() directly here would send it instantly instead of next-day.
        pass

    elif stage_name in ('offered', 'offer'):
        _send(
            application,
            subject='An offer is on its way',
            body=(f'Hi {application.candidate.first_name},\n\n'
                  f'Congratulations — an offer for {application.job_posting.title} is being prepared. '
                  f'You will receive it shortly.\n\nRegards,\n{application.job_posting.company}'),
        )


def _send(application, subject, body):
    candidate_email = application.candidate.email
    if not candidate_email:
        return
    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        recipient_list=[candidate_email],
        fail_silently=True,
    )