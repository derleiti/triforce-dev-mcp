"""
OpenRouter Budget Guard v1.0
=============================
Tracked daily/monthly spend mit Auto-Pause.
Nutzt lokales JSON-File statt Redis (simpel, persistent).

Usage:
    from app.services.openrouter_budget import budget_guard
    
    if not budget_guard.can_spend():
        raise BudgetExceeded("OpenRouter daily/monthly limit reached")
    
    # ... nach dem Call:
    budget_guard.track_spend(cost_usd=0.003)
"""

import json
import os
import logging
from datetime import datetime, date
from pathlib import Path

from app.paths import DATA_DIR
from typing import Dict, Optional

logger = logging.getLogger("ailinux.openrouter_budget")

BUDGET_FILE = DATA_DIR / "openrouter_budget.json"
BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)


class OpenRouterBudgetGuard:
    def __init__(self):
        self.daily_limit = float(os.getenv("OPENROUTER_DAILY_BUDGET", "2.0"))
        self.monthly_limit = float(os.getenv("OPENROUTER_MONTHLY_BUDGET", "15.0"))
        self.warn_pct = int(os.getenv("OPENROUTER_WARN_AT_PERCENT", "80"))
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if BUDGET_FILE.exists():
                with open(BUDGET_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Budget file corrupt, resetting: {e}")
        return {"daily": {}, "monthly": {}, "total": 0.0}

    def _save(self):
        try:
            with open(BUDGET_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"Budget save failed: {e}")

    def _today(self) -> str:
        return date.today().isoformat()

    def _month(self) -> str:
        return date.today().strftime("%Y-%m")

    @property
    def daily_spend(self) -> float:
        return self._data.get("daily", {}).get(self._today(), 0.0)

    @property
    def monthly_spend(self) -> float:
        return self._data.get("monthly", {}).get(self._month(), 0.0)

    def can_spend(self) -> bool:
        """Check ob noch Budget da ist."""
        if self.daily_spend >= self.daily_limit:
            logger.warning(
                f"OpenRouter DAILY budget exhausted: "
                f"${self.daily_spend:.3f} >= ${self.daily_limit:.2f}"
            )
            return False
        if self.monthly_spend >= self.monthly_limit:
            logger.warning(
                f"OpenRouter MONTHLY budget exhausted: "
                f"${self.monthly_spend:.3f} >= ${self.monthly_limit:.2f}"
            )
            return False
        return True

    def track_spend(self, cost_usd: float):
        """Tracke Ausgabe nach einem API-Call."""
        if cost_usd <= 0:
            return

        today = self._today()
        month = self._month()

        if "daily" not in self._data:
            self._data["daily"] = {}
        if "monthly" not in self._data:
            self._data["monthly"] = {}

        self._data["daily"][today] = self._data["daily"].get(today, 0.0) + cost_usd
        self._data["monthly"][month] = self._data["monthly"].get(month, 0.0) + cost_usd
        self._data["total"] = self._data.get("total", 0.0) + cost_usd

        self._save()

        # Warn-Check
        daily_pct = (self._data["daily"][today] / self.daily_limit * 100) if self.daily_limit > 0 else 0
        monthly_pct = (self._data["monthly"][month] / self.monthly_limit * 100) if self.monthly_limit > 0 else 0

        if daily_pct >= self.warn_pct:
            logger.warning(
                f"OpenRouter daily spend at {daily_pct:.0f}%: "
                f"${self._data['daily'][today]:.3f} / ${self.daily_limit:.2f}"
            )
        if monthly_pct >= self.warn_pct:
            logger.warning(
                f"OpenRouter monthly spend at {monthly_pct:.0f}%: "
                f"${self._data['monthly'][month]:.3f} / ${self.monthly_limit:.2f}"
            )

    def get_status(self) -> Dict:
        """Status für Monitoring/API."""
        return {
            "daily_spend": round(self.daily_spend, 4),
            "daily_limit": self.daily_limit,
            "daily_remaining": round(max(0, self.daily_limit - self.daily_spend), 4),
            "monthly_spend": round(self.monthly_spend, 4),
            "monthly_limit": self.monthly_limit,
            "monthly_remaining": round(max(0, self.monthly_limit - self.monthly_spend), 4),
            "total_tracked": round(self._data.get("total", 0.0), 4),
            "can_spend": self.can_spend(),
        }

    def cleanup_old(self, keep_days: int = 30):
        """Alte Daily-Einträge aufräumen."""
        cutoff = date.today().isoformat()
        old_keys = [k for k in self._data.get("daily", {}) if k < cutoff[:8]]
        # Keep last N days
        daily = self._data.get("daily", {})
        sorted_keys = sorted(daily.keys())
        if len(sorted_keys) > keep_days:
            for k in sorted_keys[:-keep_days]:
                del daily[k]
            self._save()


# Singleton
budget_guard = OpenRouterBudgetGuard()
