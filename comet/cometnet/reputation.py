"""
CometNet Reputation Module

Implements the reputation system for tracking peer trustworthiness.
"""

import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from comet.core.models import settings

_DEFAULT_MAX_PEERS = 10_000


@dataclass
class PeerReputation:
    """Tracks reputation and metadata for a single peer."""

    reputation: float = field(
        default_factory=lambda: settings.COMETNET_REPUTATION_INITIAL
    )
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    valid_contributions: int = 0
    invalid_contributions: int = 0

    @property
    def anciennety_days(self) -> float:
        """Returns the number of days since first seen."""
        return (time.time() - self.first_seen) / 86400.0

    @property
    def anciennety_bonus(self) -> float:
        """Returns the reputation bonus from anciennety."""
        return min(
            self.anciennety_days
            * settings.COMETNET_REPUTATION_BONUS_PER_DAY_ANCIENNETY,
            settings.COMETNET_REPUTATION_BONUS_MAX_ANCIENNETY,
        )

    @property
    def effective_reputation(self) -> float:
        """Returns the effective reputation including anciennety bonus."""
        return min(
            self.reputation + self.anciennety_bonus, settings.COMETNET_REPUTATION_MAX
        )

    @property
    def trust_level(self) -> str:
        """Returns the trust level as a string."""
        score = self.effective_reputation
        if score < settings.COMETNET_REPUTATION_THRESHOLD_UNTRUSTED:
            return "untrusted"
        if score < settings.COMETNET_REPUTATION_THRESHOLD_TRUSTED:
            return "neutral"
        return "trusted"

    def is_acceptable(self) -> bool:
        """Return whether messages from this peer should be processed."""
        return (
            self.effective_reputation
            >= settings.COMETNET_REPUTATION_THRESHOLD_UNTRUSTED
        )

    def update_seen(self) -> None:
        """Update the last seen timestamp."""
        self.last_seen = time.time()

    def add_valid_contribution(self, count: int = 1) -> None:
        """Add valid contribution(s) and update reputation."""
        self.valid_contributions += count
        self._adjust_reputation(
            settings.COMETNET_REPUTATION_BONUS_VALID_CONTRIBUTION * count
        )

    def add_invalid_contribution(self, count: int = 1) -> None:
        """Add invalid contribution(s) and update reputation."""
        self.invalid_contributions += count
        self._adjust_reputation(
            -settings.COMETNET_REPUTATION_PENALTY_INVALID_CONTRIBUTION * count
        )

    def add_signature_failure_penalty(self) -> None:
        """Apply invalid signature penalty to reputation."""
        self._adjust_reputation(-settings.COMETNET_REPUTATION_PENALTY_INVALID_SIGNATURE)

    def _adjust_reputation(self, delta: float) -> None:
        """Adjust reputation by delta, clamping to valid range."""
        self.reputation = max(
            settings.COMETNET_REPUTATION_MIN,
            min(settings.COMETNET_REPUTATION_MAX, self.reputation + delta),
        )


class ReputationStore:
    """
    Manages reputation for all known peers.

    This is an in-memory store that can be persisted to disk.
    """

    def __init__(self, max_peers: int = _DEFAULT_MAX_PEERS):
        if max_peers <= 0:
            raise ValueError("max_peers must be positive")
        self.max_peers = max_peers
        self._peers: OrderedDict[str, PeerReputation] = OrderedDict()

    @staticmethod
    def _validate_node_id(node_id: object) -> str:
        if type(node_id) is not str or not node_id:
            raise ValueError("node_id must be a non-empty string")
        return node_id

    @classmethod
    def _peer_from_persisted(
        cls, node_id: object, value: object
    ) -> tuple[str, PeerReputation]:
        node_id = cls._validate_node_id(node_id)
        required_fields = {
            "reputation",
            "first_seen",
            "last_seen",
            "valid_contributions",
            "invalid_contributions",
        }
        if type(value) is not dict or not required_fields <= value.keys():
            raise ValueError("persisted reputation peer does not match current schema")

        reputation = value["reputation"]
        if (
            type(reputation) not in (int, float)
            or not math.isfinite(reputation)
            or not settings.COMETNET_REPUTATION_MIN
            <= reputation
            <= settings.COMETNET_REPUTATION_MAX
        ):
            raise ValueError("persisted reputation must be finite and within bounds")

        first_seen = value["first_seen"]
        last_seen = value["last_seen"]
        if any(
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp < 0
            for timestamp in (first_seen, last_seen)
        ):
            raise ValueError(
                "persisted reputation timestamps must be finite and non-negative"
            )
        if last_seen < first_seen:
            raise ValueError("persisted reputation last_seen cannot precede first_seen")

        for field_name in ("valid_contributions", "invalid_contributions"):
            count = value[field_name]
            if type(count) is not int or count < 0:
                raise ValueError(
                    f"persisted {field_name} must be a non-negative integer"
                )

        return node_id, PeerReputation(
            reputation=reputation,
            first_seen=first_seen,
            last_seen=last_seen,
            valid_contributions=value["valid_contributions"],
            invalid_contributions=value["invalid_contributions"],
        )

    def get_or_create(self, node_id: str) -> PeerReputation:
        """Get an existing peer reputation or create a new one."""
        peer = self._peers.get(node_id)
        if peer is None:
            if len(self._peers) >= self.max_peers:
                self._peers.popitem(last=False)
            peer = PeerReputation()
            self._peers[node_id] = peer
        else:
            self._peers.move_to_end(node_id)
        return peer

    def get(self, node_id: str) -> PeerReputation | None:
        """Get peer reputation if it exists."""
        return self._peers.get(node_id)

    def is_peer_acceptable(self, node_id: str) -> bool:
        """Check if a peer is above the untrusted threshold."""
        peer = self._peers.get(node_id)
        if peer is None:
            # New peers are acceptable
            self.get_or_create(node_id)
            return True
        return peer.is_acceptable()

    def cleanup_old_peers(self, max_age_days: float = 30.0) -> int:
        """Remove peers that haven't been seen in a while."""
        cutoff = time.time() - (max_age_days * 86400)
        to_remove = [
            node_id for node_id, peer in self._peers.items() if peer.last_seen < cutoff
        ]
        for node_id in to_remove:
            del self._peers[node_id]
        return len(to_remove)

    def to_dict(self) -> dict:
        """Serialize the store to a dictionary for persistence."""
        return {
            "peers": {
                node_id: {
                    "reputation": peer.reputation,
                    "first_seen": peer.first_seen,
                    "last_seen": peer.last_seen,
                    "valid_contributions": peer.valid_contributions,
                    "invalid_contributions": peer.invalid_contributions,
                }
                for node_id, peer in sorted(self._peers.items())
            },
        }

    @classmethod
    def _decode_persisted(
        cls,
        data: object,
        *,
        max_peers: int,
    ) -> OrderedDict[str, PeerReputation]:
        if type(data) is not dict or "peers" not in data:
            raise ValueError("reputation store does not match the current schema")
        if type(data["peers"]) is not dict:
            raise ValueError("reputation peers container is invalid")
        peers = (
            cls._peer_from_persisted(node_id, value)
            for node_id, value in data["peers"].items()
        )
        decoded = OrderedDict(sorted(peers, key=lambda item: item[1].last_seen))
        while len(decoded) > max_peers:
            decoded.popitem(last=False)
        return decoded

    @classmethod
    def validate_persisted(
        cls,
        data: object,
        *,
        max_peers: int = _DEFAULT_MAX_PEERS,
    ) -> None:
        """Validate a complete persisted candidate without publishing it."""
        cls._decode_persisted(data, max_peers=max_peers)

    def from_dict(self, data: dict) -> None:
        """Load the store from a dictionary."""
        self._peers = self._decode_persisted(data, max_peers=self.max_peers)
