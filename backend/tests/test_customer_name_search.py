from sales_backend.domain.customer_search import matching_names, name_search_keys, phonetic_query


def test_chinese_full_pinyin_initials_and_non_chinese_parts():
    names = ["商汤科技", "重庆数字科技", "ACME上海2号", "另一家公司"]
    for query in ("shangtang", "ST", "shang tang", "ＳＨＡＮＧＴＡＮＧ"):
        assert matching_names(names, phonetic_query(query)) == ["商汤科技"]
    assert matching_names(names, "chongqing") == ["重庆数字科技"]
    assert matching_names(names, "cq") == ["重庆数字科技"]
    assert matching_names(names, "acmeshanghai2") == ["ACME上海2号"]
    assert matching_names(names, "missing") == []
    assert phonetic_query("商汤") == phonetic_query("%_") == phonetic_query("---") == ""


def test_cache_is_only_text_projection_never_a_customer_directory():
    assert name_search_keys.cache_info().maxsize == 50000
    assert matching_names(["商汤科技"], "st") == ["商汤科技"]
    # Renaming, revocation and workspace changes supply fresh authorized names.
    assert matching_names(["其他公司"], "st") == []
    assert matching_names([], "st") == []
