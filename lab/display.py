def format_verdict(verdict, width=120):
    if not verdict:
        return "verdict: n/a (mode off)"
    lines = [
        f"verdict: {verdict['status']}  score={verdict['score']:.2f}  passed={verdict['passed']}  "
        f"regenerations={verdict.get('rounds', 0)}  repaired={verdict.get('repaired', False)}  "
        f"{verdict.get('duration_ms', 0)}ms" + ("  TIMED OUT" if verdict.get("timed_out") else ""),
        "  checks: " + "  ".join(
            f"{c['name']}:{c['status']}({c['ms']}ms{', ' + c['note'] if c.get('note') else ''})"
            for c in verdict.get("checks", [])
        ),
    ]
    issues = verdict.get("issues", [])
    if not issues:
        lines.append("  issues: none")
    for issue in issues:
        text = f"  [{issue['severity']:<7}] {issue['code']}: {issue['message']}"
        lines.append(text if len(text) <= width else text[: width - 1] + "…")
    return "\n".join(lines)
