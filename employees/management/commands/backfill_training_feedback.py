from django.core.management.base import BaseCommand
from employees.models import EmployeeProfile, TrainingFeedback


class Command(BaseCommand):
    help = (
        "Creates a TrainingFeedback record (default 30-day duration) for any "
        "employee whose status is ONBOARDING but who doesn't have one yet. "
        "Needed for employees onboarded before the training-feedback feature "
        "existed."
    )

    def handle(self, *args, **options):
        profiles = EmployeeProfile.objects.filter(status='ONBOARDING').select_related('user')
        created = 0
        skipped = 0
        for profile in profiles:
            user = profile.user
            if hasattr(user, 'training_feedback'):
                skipped += 1
                continue
            TrainingFeedback.objects.create(
                employee=user,
                training_start_date=user.date_joined_company,
                training_duration_days=30,
            )
            created += 1
            self.stdout.write(f"Created training record for {user} (id={user.id})")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created} training record(s), {skipped} already had one."
        ))
