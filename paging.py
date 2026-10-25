def pager(total, page, per, options):
    """Pagination state: clamped page, row range and a page-number window with None for gaps."""
    pages = max((total + per - 1) // per, 1)
    page = min(max(page, 1), pages)
    near = sorted({1, pages, *range(max(page - 1, 1), min(page + 1, pages) + 1)})
    window, prev = [], 0
    for n in near:
        if prev and n - prev > 1:
            window.append(None)  # shown as an ellipsis
        window.append(n)
        prev = n
    return dict(page=page, pages=pages, total=total, per=per, options=options, window=window,
                start=(page - 1) * per + 1 if total else 0, end=min(page * per, total))
