from tools.build_preflop_table import generate


def test_preflop_table_is_seeded_and_has_169_classes():
    first = generate(10, 77)
    second = generate(10, 77)
    assert first == second
    assert first["class_count"] == 169
    assert first["samples_per_class"] == 10
    assert len(first["percentiles"]) == 169
