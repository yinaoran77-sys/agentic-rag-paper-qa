"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

为什么迁移脚本要长这样？
- upgrade(): 写"升级"操作（建表、加字段）
- downgrade(): 写"回滚"操作（删表、删字段）
- Alembic 自动生成的内容在 `pass` 的位置，你需要手动补充
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# ============================================================
# Revision 标识（不要改！）
# ============================================================
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


# ============================================================
# 升级操作（alembic upgrade）
# ============================================================
def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


# ============================================================
# 回滚操作（alembic downgrade）
# ============================================================
def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
