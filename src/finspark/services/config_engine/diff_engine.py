"""Configuration diff engine - compares two configurations and identifies changes."""

from typing import Any

from finspark.schemas.configurations import ConfigDiffItem, ConfigDiffResponse

# Fields used to identify items in a list by identity rather than position.
# Keys are matched in order — the first one found in the dict is used.
_IDENTITY_KEYS = ("source_field", "path", "name", "id", "source")


def _identity_key(item: Any) -> Any:
    """Return a stable identity key for a list item, or None if not applicable."""
    if not isinstance(item, dict):
        return None
    for key in _IDENTITY_KEYS:
        if key in item:
            return item[key]
    return None


class ConfigDiffEngine:
    """Compares two configurations and produces a structured diff."""

    BREAKING_PATHS = {
        "auth.type",
        "base_url",
        "version",
        "endpoints",
    }

    def compare(
        self,
        config_a: dict[str, Any],
        config_b: dict[str, Any],
        config_a_id: str = "a",
        config_b_id: str = "b",
    ) -> ConfigDiffResponse:
        """Compare two configurations and return structured diff."""
        diffs: list[ConfigDiffItem] = []
        self._diff_recursive(config_a, config_b, "", diffs)

        breaking = sum(1 for d in diffs if d.is_breaking)

        return ConfigDiffResponse(
            config_a_id=config_a_id,
            config_b_id=config_b_id,
            total_changes=len(diffs),
            breaking_changes=breaking,
            diffs=diffs,
        )

    def _diff_recursive(
        self,
        a: Any,
        b: Any,
        path: str,
        diffs: list[ConfigDiffItem],
    ) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            all_keys = set(a.keys()) | set(b.keys())
            for key in sorted(all_keys):
                child_path = f"{path}.{key}" if path else key
                if key not in a:
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="added",
                            new_value=b[key],
                            is_breaking=self._is_breaking(child_path),
                        )
                    )
                elif key not in b:
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="removed",
                            old_value=a[key],
                            is_breaking=self._is_breaking(child_path),
                        )
                    )
                else:
                    self._diff_recursive(a[key], b[key], child_path, diffs)

        elif isinstance(a, list) and isinstance(b, list):
            self._diff_lists(a, b, path, diffs)

        elif a != b:
            diffs.append(
                ConfigDiffItem(
                    path=path,
                    change_type="modified",
                    old_value=a,
                    new_value=b,
                    is_breaking=self._is_breaking(path),
                )
            )

    def _diff_lists(
        self,
        a: list[Any],
        b: list[Any],
        path: str,
        diffs: list[ConfigDiffItem],
    ) -> None:
        """Diff two lists using identity-based matching when items have a key field."""
        # Check whether the items are identity-keyed dicts
        a_keys = [_identity_key(item) for item in a]
        b_keys = [_identity_key(item) for item in b]

        if any(k is not None for k in a_keys) or any(k is not None for k in b_keys):
            # Identity-based matching
            a_by_key: dict[Any, Any] = {}
            for idx, (item, key) in enumerate(zip(a, a_keys, strict=True)):
                k = key if key is not None else f"__pos_{idx}"
                a_by_key[k] = item

            b_by_key: dict[Any, Any] = {}
            for idx, (item, key) in enumerate(zip(b, b_keys, strict=True)):
                k = key if key is not None else f"__pos_{idx}"
                b_by_key[k] = item

            all_identity_keys = list(a_by_key.keys()) + [
                k for k in b_by_key if k not in a_by_key
            ]
            for ik in all_identity_keys:
                child_path = f"{path}[{ik}]"
                if ik not in a_by_key:
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="added",
                            new_value=b_by_key[ik],
                            is_breaking=self._is_breaking(path),
                        )
                    )
                elif ik not in b_by_key:
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="removed",
                            old_value=a_by_key[ik],
                            is_breaking=self._is_breaking(path),
                        )
                    )
                else:
                    self._diff_recursive(a_by_key[ik], b_by_key[ik], child_path, diffs)
        else:
            # Positional matching for plain-value lists
            max_len = max(len(a), len(b))
            for i in range(max_len):
                child_path = f"{path}[{i}]"
                if i >= len(a):
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="added",
                            new_value=b[i],
                            is_breaking=self._is_breaking(path),
                        )
                    )
                elif i >= len(b):
                    diffs.append(
                        ConfigDiffItem(
                            path=child_path,
                            change_type="removed",
                            old_value=a[i],
                            is_breaking=self._is_breaking(path),
                        )
                    )
                else:
                    self._diff_recursive(a[i], b[i], child_path, diffs)

    def _is_breaking(self, path: str) -> bool:
        """Check if a change at this path is a breaking change.

        Uses exact segment matching to avoid false positives where a breaking
        path name is a prefix of a non-breaking one (e.g. 'version' must not
        match 'version_info').
        """
        path_segments = path.split(".")
        for bp in self.BREAKING_PATHS:
            bp_segments = bp.split(".")
            n = len(bp_segments)
            # The path matches if it equals the breaking path or starts with it
            # at a segment boundary.
            if path_segments[:n] == bp_segments:
                return True
        return False
