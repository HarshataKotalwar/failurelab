"""Add the controlled failure experiment lifecycle fields."""

import sqlalchemy as sa
from alembic import op

revision = "0002_experiment_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("target_service", sa.String(100), nullable=True))
    op.add_column("experiments", sa.Column("failure_type", sa.String(50), nullable=True))
    op.add_column("experiments", sa.Column("duration_seconds", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE experiments AS experiment
        SET target_service = COALESCE(
                (
                    SELECT service.name
                    FROM services AS service
                    WHERE service.id = experiment.service_id
                ),
                'payment-service'
            ),
            failure_type = 'unavailable',
            duration_seconds = 60
        """
    )
    op.alter_column("experiments", "target_service", nullable=False)
    op.alter_column("experiments", "failure_type", nullable=False)
    op.alter_column("experiments", "duration_seconds", nullable=False)
    op.alter_column("experiments", "service_id", nullable=True)
    op.alter_column("experiments", "scenario", nullable=True)
    op.create_index(
        "uq_experiments_one_running",
        "experiments",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_experiments_one_running", table_name="experiments")
    op.execute(
        """
        INSERT INTO services (id, name, url, status)
        SELECT gen_random_uuid(), 'payment-service', 'http://payment-service:8003', 'configured'
        WHERE EXISTS (SELECT 1 FROM experiments WHERE service_id IS NULL)
          AND NOT EXISTS (SELECT 1 FROM services WHERE name = 'payment-service')
        """
    )
    op.execute(
        """
        UPDATE experiments
        SET service_id = (SELECT id FROM services WHERE name = 'payment-service'),
            scenario = COALESCE(scenario, failure_type)
        WHERE service_id IS NULL OR scenario IS NULL
        """
    )
    op.alter_column("experiments", "service_id", nullable=False)
    op.alter_column("experiments", "scenario", nullable=False)
    op.drop_column("experiments", "duration_seconds")
    op.drop_column("experiments", "failure_type")
    op.drop_column("experiments", "target_service")
