from __future__ import annotations

from ..news import classify_news_text
from ..models import AgentSignal, StockSnapshot


NEGATIVE_KEYWORDS = ("investigation", "penalty", "delist", "fraud", "major_reduction", "立案调查", "行政处罚", "退市", "减持")
POSITIVE_KEYWORDS = ("buyback", "earnings_beat", "contract", "policy_support", "回购", "增持", "中标", "预增")


class InformationAgent:
    name = "information"

    def score(self, snapshot: StockSnapshot) -> AgentSignal:
        if snapshot.news_evidence:
            score = float(snapshot.news_evidence.get("score", 0.55))
            confidence = float(snapshot.news_evidence.get("confidence", 0.50))
            risk_flag = bool(snapshot.news_evidence.get("material_risk")) or snapshot.news_evidence.get("severity") == "high"
            headline = str(snapshot.news_evidence.get("headline") or snapshot.news_evidence.get("summary") or "news_evidence")
            reason = f"{snapshot.news_evidence.get('polarity', 'neutral')}_news={headline}"
            return AgentSignal(
                self.name,
                snapshot.code,
                score,
                confidence,
                risk_flag,
                reason,
                {"news_evidence": snapshot.news_evidence},
            )

        if not snapshot.info_risk:
            return AgentSignal(self.name, snapshot.code, 0.55, 0.45, False, "no_material_event")
        text = snapshot.info_risk.lower()
        polarity, severity, confidence, keywords, _ = classify_news_text(snapshot.info_risk)
        if polarity == "negative":
            score = 0.0 if severity == "high" else 0.15 if severity == "medium" else 0.35
            return AgentSignal(
                self.name,
                snapshot.code,
                score,
                confidence,
                severity == "high",
                f"negative_event={snapshot.info_risk}",
                {"keywords": keywords, "severity": severity},
            )
        if polarity == "positive":
            return AgentSignal(
                self.name,
                snapshot.code,
                0.75,
                confidence,
                False,
                f"positive_event={snapshot.info_risk}",
                {"keywords": keywords, "severity": severity},
            )
        if any(keyword in text for keyword in NEGATIVE_KEYWORDS):
            return AgentSignal(self.name, snapshot.code, 0.0, 0.90, True, f"negative_event={snapshot.info_risk}")
        if any(keyword in text for keyword in POSITIVE_KEYWORDS):
            return AgentSignal(self.name, snapshot.code, 0.75, 0.70, False, f"positive_event={snapshot.info_risk}")
        return AgentSignal(self.name, snapshot.code, 0.50, 0.55, False, f"neutral_event={snapshot.info_risk}")
