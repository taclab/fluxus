"""Intervalle de récupération fondé sur le rythme de publication : plafond ramené de 24 h à 4 h.

Les flux adaptatifs bloqués au-delà du nouveau plafond y sont ramenés, et leur prochaine récupération
est avancée, étalée sur 30 minutes pour ne pas tout relire d'un coup. Les flux en erreur gardent leur
recul, les intervalles fixes ne bougent pas.

Revision ID: 3c7e9a41d2b8
Revises: b230ccdf5f53
Create Date: 2026-10-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = '3c7e9a41d2b8'
down_revision: Union[str, None] = 'b230ccdf5f53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE feeds
        SET fetch_interval_min = 240,
            next_fetch_at = LEAST(next_fetch_at, now() + random() * interval '30 minutes')
        WHERE fetch_interval_fixed_min IS NULL AND fetch_interval_min > 240 AND error_count = 0
        """
    )


def downgrade() -> None:
    # Données seulement : l'ancien algorithme ré-espacera de lui-même.
    pass
