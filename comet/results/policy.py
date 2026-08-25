"""Compile and evaluate bounded release rules with one shared fact registry."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from comet.results.config import (
    FilterConfig,
    KeywordPattern,
    LanguagesConfig,
    PolicyPredicate,
    PolicyRule,
    ResultsConfig,
)
from comet.results.facts import (
    FACT_REGISTRY,
    CacheState,
    FactPhase,
    ReleaseFacts,
    ResultEntry,
    fact_value,
    normalize_fact_selector,
    normalize_search_text,
)


class RejectKind(StrEnum):
    GUARD = "guard"
    TRASH = "trash"
    FACET = "facet"
    RANGE = "range"
    KEYWORD = "keyword"
    LANGUAGE = "language"
    RULE = "rule"
    SELECTION = "selection"


@dataclass(frozen=True, slots=True)
class RejectReason:
    identifier: int
    kind: RejectKind
    field: str
    label: str


class RejectionCollector:
    """Dense optional counters; the disabled path allocates nothing per result."""

    __slots__ = ("_counts",)

    def __init__(self, reason_count: int, *, enabled: bool):
        self._counts = [0] * reason_count if enabled else None

    def add(self, identifier: int) -> None:
        if self._counts is not None and identifier:
            self._counts[identifier - 1] += 1

    def add_count(self, identifier: int, count: int) -> None:
        if self._counts is not None and identifier and count > 0:
            self._counts[identifier - 1] += count

    @property
    def counts(self) -> tuple[int, ...]:
        return tuple(self._counts or ())


def _unknown(value) -> bool:
    return value is None or value == "" or value == () or value == frozenset()


def _equals(left, right) -> bool:
    if _unknown(left):
        return right == "unknown"
    if isinstance(left, (set, frozenset, tuple, list)):
        return right in left
    return left == right


def _one_of(left, right) -> bool:
    if _unknown(left):
        return "unknown" in right
    if isinstance(left, (set, frozenset, tuple, list)):
        return not right.isdisjoint(left)
    return left in right


def _contains(left, right) -> bool:
    if _unknown(left) or not isinstance(right, str) or not right:
        return False
    if isinstance(left, (set, frozenset, tuple, list)):
        return any(isinstance(value, str) and right in value for value in left)
    return isinstance(left, str) and right in left


@dataclass(frozen=True, slots=True)
class CompiledPredicate:
    field: str
    op: str
    value: object = None
    values: frozenset[object] | tuple[object, ...] = frozenset()

    @property
    def phase(self) -> FactPhase:
        return FACT_REGISTRY[self.field].phase

    def matches(
        self,
        facts: ReleaseFacts,
        entry: ResultEntry | None,
        now_ms: int,
    ) -> bool:
        actual = fact_value(facts, self.field, entry=entry, now_ms=now_ms)
        if self.op in {"contains", "notContains"}:
            if self.field == "title":
                actual = facts.keyword_title
            elif self.field == "releaseGroup":
                actual = facts.keyword_release_group
            elif self.field == "source":
                actual = facts.keyword_source
        if self.op == "known":
            return not _unknown(actual)
        if self.op == "unknown":
            return _unknown(actual)
        if self.op == "is":
            return _equals(actual, self.value)
        if self.op == "isNot":
            return not _equals(actual, self.value)
        if self.op == "oneOf":
            return _one_of(actual, self.values)
        if self.op == "noneOf":
            return not _one_of(actual, self.values)
        if self.op == "contains":
            return _contains(actual, self.value)
        if self.op == "notContains":
            return not _contains(actual, self.value)
        if _unknown(actual) or isinstance(actual, bool):
            return False
        if self.op == "lt":
            return actual < self.value
        if self.op == "lte":
            return actual <= self.value
        if self.op == "gt":
            return actual > self.value
        if self.op == "gte":
            return actual >= self.value
        if self.op == "between":
            return self.values[0] <= actual <= self.values[1]
        raise AssertionError(f"uncompiled predicate operator: {self.op}")


@dataclass(frozen=True, slots=True)
class CompiledRule:
    action: str
    predicates: tuple[CompiledPredicate, ...]
    reject_id: int
    preference_index: int = -1
    language: str | None = None

    @property
    def phase(self) -> FactPhase:
        return (
            FactPhase.LATE
            if any(predicate.phase is FactPhase.LATE for predicate in self.predicates)
            else FactPhase.EARLY
        )

    def matches(
        self,
        facts: ReleaseFacts,
        entry: ResultEntry | None,
        now_ms: int,
    ) -> bool:
        return all(
            predicate.matches(facts, entry, now_ms) for predicate in self.predicates
        )


@dataclass(frozen=True, slots=True)
class CompiledGuard:
    predicate: CompiledPredicate
    reject_id: int
    reject_on_match: bool
    scope: str = "all"

    @property
    def phase(self) -> FactPhase:
        if self.scope in {"cached", "uncached", "directTorrent", "needsDownload"}:
            return FactPhase.LATE
        return self.predicate.phase

    def rejects(
        self,
        facts: ReleaseFacts,
        entry: ResultEntry | None,
        now_ms: int,
    ) -> bool:
        if not in_scope(self.scope, facts, entry):
            return False
        matched = self.predicate.matches(facts, entry, now_ms)
        return matched if self.reject_on_match else not matched


def in_scope(
    scope: str,
    facts: ReleaseFacts,
    entry: ResultEntry | None,
) -> bool:
    if scope == "all":
        return True
    if scope == "torrent":
        return facts.transport == "torrent"
    if scope == "usenet":
        return facts.transport == "usenet"
    if entry is None:
        return False
    if scope == "directTorrent":
        return entry.provider_kind == "direct_torrent"
    if scope == "cached":
        return entry.cache_state is CacheState.CACHED
    if scope == "uncached":
        return entry.cache_state is CacheState.UNCACHED
    if scope == "needsDownload":
        return (
            entry.provider_kind == "direct_torrent"
            or entry.cache_state is CacheState.UNCACHED
        )
    raise AssertionError(f"unknown compiled scope: {scope}")


def _normalize_wildcard(value: str) -> str:
    output = []
    pending_separator = False
    for character in unicodedata.normalize("NFKC", value).casefold():
        if character in "*?":
            if pending_separator and output and output[-1] != " ":
                output.append(" ")
            output.append(character)
            pending_separator = False
        elif character.isalnum():
            if pending_separator and output and output[-1] not in {" ", "*"}:
                output.append(" ")
            output.append(character)
            pending_separator = False
        else:
            pending_separator = True
    return "".join(output).strip()


def _wildcard_match(pattern: str, text: str) -> bool:
    """Linear glob matcher with bounded backtracking to the latest star."""
    pattern_index = text_index = 0
    star_index = -1
    retry_index = 0
    while text_index < len(text):
        if pattern_index < len(pattern) and pattern[pattern_index] in {
            "?",
            text[text_index],
        }:
            pattern_index += 1
            text_index += 1
        elif pattern_index < len(pattern) and pattern[pattern_index] == "*":
            star_index = pattern_index
            retry_index = text_index
            pattern_index += 1
        elif star_index >= 0:
            pattern_index = star_index + 1
            retry_index += 1
            text_index = retry_index
        else:
            return False
    while pattern_index < len(pattern) and pattern[pattern_index] == "*":
        pattern_index += 1
    return pattern_index == len(pattern)


@dataclass(frozen=True, slots=True)
class CompiledKeyword:
    pattern: str
    mode: str
    target: str

    def matches(self, facts: ReleaseFacts) -> bool:
        text = {
            "title": facts.keyword_title,
            "releaseGroup": facts.keyword_release_group,
            "source": facts.keyword_source,
        }[self.target]
        if self.mode == "word":
            return self.pattern in text.split()
        if self.mode == "phrase":
            return self.pattern in text
        return _wildcard_match(self.pattern, text)


def _compile_keyword(pattern: KeywordPattern) -> CompiledKeyword:
    value = (
        _normalize_wildcard(pattern.value)
        if pattern.mode == "wildcard"
        else normalize_search_text(pattern.value)
    )
    if not value:
        raise ValueError("keyword normalizes to an empty value")
    if pattern.mode == "word" and " " in value:
        raise ValueError("word keyword must contain exactly one token")
    return CompiledKeyword(value, pattern.mode, pattern.target)


def _compile_predicate(predicate: PolicyPredicate) -> CompiledPredicate:
    def operand(value):
        if not isinstance(value, str):
            return value
        if predicate.op in {"contains", "notContains"}:
            return normalize_search_text(value)
        return normalize_fact_selector(predicate.field, value)

    values = tuple(operand(value) for value in (predicate.values or ()))
    return CompiledPredicate(
        field=predicate.field,
        op=predicate.op,
        value=operand(predicate.value),
        values=values if predicate.op == "between" else frozenset(values),
    )


def _compile_rule(
    rule: PolicyRule, reject_id: int, preference_index: int
) -> CompiledRule:
    compiled = CompiledRule(
        action=rule.action,
        predicates=tuple(_compile_predicate(item) for item in rule.all),
        reject_id=reject_id,
        preference_index=preference_index,
        language=rule.language.casefold() if rule.language else None,
    )
    if compiled.action == "addLanguage" and compiled.phase is FactPhase.LATE:
        raise ValueError("addLanguage may only use early facts")
    return compiled


class _Compiler:
    def __init__(self):
        self.reasons: list[RejectReason] = []

    def reason(self, kind: RejectKind, field: str, label: str) -> int:
        identifier = len(self.reasons) + 1
        self.reasons.append(RejectReason(identifier, kind, field, label))
        return identifier

    def facets(self, filters: FilterConfig) -> list[CompiledGuard]:
        guards = []
        for field, facet in filters.dimensions:
            if facet.only:
                guards.append(
                    CompiledGuard(
                        CompiledPredicate(
                            field,
                            "oneOf",
                            values=frozenset(
                                normalize_fact_selector(field, item)
                                for item in facet.only
                            ),
                        ),
                        self.reason(RejectKind.FACET, field, "only"),
                        False,
                    )
                )
            if facet.exclude:
                guards.append(
                    CompiledGuard(
                        CompiledPredicate(
                            field,
                            "oneOf",
                            values=frozenset(
                                normalize_fact_selector(field, item)
                                for item in facet.exclude
                            ),
                        ),
                        self.reason(RejectKind.FACET, field, "exclude"),
                        True,
                    )
                )
        return guards

    def ranges(self, filters: FilterConfig) -> list[CompiledGuard]:
        guards = []
        for field, range_config in filters.ranges:
            if range_config is None:
                continue
            if range_config.min is not None:
                guards.append(
                    CompiledGuard(
                        CompiledPredicate(field, "gte", value=range_config.min),
                        self.reason(RejectKind.RANGE, field, "min"),
                        False,
                        range_config.scope,
                    )
                )
            if range_config.max is not None:
                guards.append(
                    CompiledGuard(
                        CompiledPredicate(field, "lte", value=range_config.max),
                        self.reason(RejectKind.RANGE, field, "max"),
                        False,
                        range_config.scope,
                    )
                )
        return guards


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    early_guards: tuple[CompiledGuard, ...]
    late_guards: tuple[CompiledGuard, ...]
    early_excludes: tuple[CompiledRule, ...]
    late_excludes: tuple[CompiledRule, ...]
    require_rules: tuple[CompiledRule, ...]
    add_languages: tuple[CompiledRule, ...]
    prefer_rules: tuple[CompiledRule, ...]
    keyword_exclude: tuple[CompiledKeyword, ...]
    keyword_require: tuple[CompiledKeyword, ...]
    keyword_prefer: tuple[CompiledKeyword, ...]
    language_required: frozenset[str]
    language_allowed: frozenset[str]
    language_excluded: frozenset[str]
    language_unknown_excluded: bool
    language_reject_id: int
    keyword_reject_id: int
    reasons: tuple[RejectReason, ...]

    @classmethod
    def compile(
        cls,
        results: ResultsConfig,
        languages: LanguagesConfig,
    ) -> ReleasePolicy:
        compiler = _Compiler()
        for field in ("title", "year", "episode", "adult", "private"):
            compiler.reason(RejectKind.GUARD, field, "correctness")
        compiler.reason(RejectKind.SELECTION, "alternatives", "hidden")
        compiler.reason(RejectKind.SELECTION, "limits", "limited")
        guards = compiler.facets(results.filters) + compiler.ranges(results.filters)
        if results.filters.removeTrash:
            guards.insert(
                0,
                CompiledGuard(
                    CompiledPredicate("trash", "is", value=True),
                    compiler.reason(RejectKind.TRASH, "trash", "trash"),
                    True,
                ),
            )
        compiled_rules = []
        preference_index = 0
        for rule in results.filters.rules:
            reject_id = (
                compiler.reason(RejectKind.RULE, "rule", rule.action)
                if rule.action in {"exclude", "require"}
                else 0
            )
            compiled_rules.append(_compile_rule(rule, reject_id, preference_index))
            if rule.action == "prefer":
                preference_index += 1

        language_active = bool(
            languages.required or languages.exclude or languages.unknown == "exclude"
        )
        language_reject_id = (
            compiler.reason(RejectKind.LANGUAGE, "languages", "eligibility")
            if language_active
            else 0
        )
        keywords = results.filters.keywords
        keyword_reject_id = (
            compiler.reason(RejectKind.KEYWORD, "title", "keyword")
            if keywords.exclude or keywords.require
            else 0
        )
        return cls(
            early_guards=tuple(
                guard for guard in guards if guard.phase is FactPhase.EARLY
            ),
            late_guards=tuple(
                guard for guard in guards if guard.phase is FactPhase.LATE
            ),
            early_excludes=tuple(
                rule
                for rule in compiled_rules
                if rule.action == "exclude" and rule.phase is FactPhase.EARLY
            ),
            late_excludes=tuple(
                rule
                for rule in compiled_rules
                if rule.action == "exclude" and rule.phase is FactPhase.LATE
            ),
            require_rules=tuple(
                rule for rule in compiled_rules if rule.action == "require"
            ),
            add_languages=tuple(
                rule for rule in compiled_rules if rule.action == "addLanguage"
            ),
            prefer_rules=tuple(
                rule for rule in compiled_rules if rule.action == "prefer"
            ),
            keyword_exclude=tuple(_compile_keyword(item) for item in keywords.exclude),
            keyword_require=tuple(_compile_keyword(item) for item in keywords.require),
            keyword_prefer=tuple(_compile_keyword(item) for item in keywords.prefer),
            language_required=frozenset(item.casefold() for item in languages.required),
            language_allowed=frozenset(item.casefold() for item in languages.allowed),
            language_excluded=frozenset(item.casefold() for item in languages.exclude),
            language_unknown_excluded=languages.unknown == "exclude",
            language_reject_id=language_reject_id,
            keyword_reject_id=keyword_reject_id,
            reasons=tuple(compiler.reasons),
        )

    @property
    def is_default_fast_path(self) -> bool:
        return not any(
            (
                self.early_guards,
                self.late_guards,
                self.early_excludes,
                self.late_excludes,
                self.require_rules,
                self.add_languages,
                self.prefer_rules,
                self.keyword_exclude,
                self.keyword_require,
                self.keyword_prefer,
                self.language_required,
                self.language_excluded,
                self.language_unknown_excluded,
            )
        )

    def aggregate_reject_id(self, field: str) -> int:
        return next(
            (
                reason.identifier
                for reason in self.reasons
                if reason.kind in {RejectKind.GUARD, RejectKind.SELECTION}
                and reason.field == field
            ),
            0,
        )

    def enrich(self, facts: ReleaseFacts, *, now_ms: int) -> ReleaseFacts:
        for rule in self.add_languages:
            if rule.matches(facts, None, now_ms):
                facts = facts.with_language(rule.language or "")
        return facts

    def evaluate_early(self, facts: ReleaseFacts, *, now_ms: int) -> int:
        for guard in self.early_guards:
            if guard.rejects(facts, None, now_ms):
                return guard.reject_id
        for rule in self.early_excludes:
            if rule.matches(facts, None, now_ms):
                return rule.reject_id
        if self.keyword_exclude and any(
            keyword.matches(facts) for keyword in self.keyword_exclude
        ):
            return self.keyword_reject_id
        if self.keyword_require and not any(
            keyword.matches(facts) for keyword in self.keyword_require
        ):
            return self.keyword_reject_id
        if (
            self.require_rules
            and all(rule.phase is FactPhase.EARLY for rule in self.require_rules)
            and not any(
                rule.matches(facts, None, now_ms) for rule in self.require_rules
            )
        ):
            return self.require_rules[0].reject_id
        languages = facts.languages
        if languages:
            if self.language_required and not languages & self.language_required:
                return self.language_reject_id
            forbidden = languages & (self.language_excluded - self.language_allowed)
            if forbidden:
                return self.language_reject_id
        return 0

    def evaluate_late(self, entry: ResultEntry, *, now_ms: int) -> int:
        for guard in self.late_guards:
            if guard.rejects(entry.facts, entry, now_ms):
                return guard.reject_id
        for rule in self.late_excludes:
            if rule.matches(entry.facts, entry, now_ms):
                return rule.reject_id
        if (
            self.require_rules
            and not all(rule.phase is FactPhase.EARLY for rule in self.require_rules)
            and not any(
                rule.matches(entry.facts, entry, now_ms) for rule in self.require_rules
            )
        ):
            return self.require_rules[0].reject_id
        languages = entry.facts.languages
        if not languages:
            if self.language_unknown_excluded or self.language_required:
                return self.language_reject_id
        else:
            if self.language_required and not languages & self.language_required:
                return self.language_reject_id
            forbidden = languages & (self.language_excluded - self.language_allowed)
            if forbidden:
                return self.language_reject_id
        return 0

    def keyword_rank(self, facts: ReleaseFacts) -> int | None:
        return next(
            (
                index
                for index, keyword in enumerate(self.keyword_prefer)
                if keyword.matches(facts)
            ),
            None,
        )

    def preference_rule_rank(self, entry: ResultEntry, *, now_ms: int) -> int | None:
        return next(
            (
                rule.preference_index
                for rule in self.prefer_rules
                if rule.matches(entry.facts, entry, now_ms)
            ),
            None,
        )
