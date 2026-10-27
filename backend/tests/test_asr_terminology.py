"""Spelling hint boundaries only; no model, network or database calls."""
from sales_backend.domain.asr_terminology import (
    NORMALIZATION_VERSION, VERSION, normalize_acronyms, provider_words,
)


def test_standard_words_stay_first_and_spelling_hints_are_stable():
    words = ('SalesBuddy', 'ACV', 'FDE', '生态ISV', 'A C V', 'ACV')
    assert VERSION == 2
    assert provider_words(words) == ('SalesBuddy', 'ACV', 'FDE', '生态ISV', 'A C V', 'F D E')
    assert words == ('SalesBuddy', 'ACV', 'FDE', '生态ISV', 'A C V', 'ACV')


def test_only_short_ascii_uppercase_acronyms_receive_hints():
    words = ('A', 'ABCDEFG', 'acv', 'AcV', 'ＡＣＶ', 'R2D2', 'ISO-9001', 'AB', 'ABCDEF')
    assert provider_words(words) == (*words, 'A B', 'A B C D E F')


def test_no_unconfigured_or_similar_sounding_terms_are_added():
    assert provider_words(()) == ()
    assert provider_words(('ACV',)) == ('ACV', 'A C V')
    assert provider_words(('RCV',)) == ('RCV', 'R C V')
    assert 'RCV' not in provider_words(('ACV',))
    assert 'ACV' not in provider_words(('RCV',))


def test_word_limit_preserves_all_standard_words_before_optional_hints():
    words = tuple(f'product-{i}' for i in range(47)) + ('ACV', 'FDE')
    assert provider_words(words) == (*words, 'A C V')
    full = (*words, 'other-product')
    assert provider_words(full) == full


def test_character_limit_counts_commas_and_does_not_drop_standard_words():
    words = tuple(f'term{i:02d}' + 'x' * 58 for i in range(30)) + ('ACV', 'p' * 40)
    assert len(','.join(words)) == 1994
    assert provider_words(words) == (*words, 'A C V')
    assert len(','.join(provider_words(words))) == 2000
    larger = (*words[:-1], 'p' * 41)
    assert provider_words(larger) == larger


def test_normalization_records_original_offsets_and_accepts_cjk_neighbors():
    original = '安排F D E，核对A C V。'
    assert NORMALIZATION_VERSION == 1
    assert normalize_acronyms(original, ('FDE', 'ACV')) == (
        '安排FDE，核对ACV。',
        [{'start': 2, 'end': 7, 'before': 'F D E', 'after': 'FDE'},
         {'start': 10, 'end': 15, 'before': 'A C V', 'after': 'ACV'}],
    )
    assert original == '安排F D E，核对A C V。'


def test_normalization_rejects_substrings_identifiers_and_different_letters():
    ambiguous = (
        'X A C V', 'A C V B', '1A C V', 'A C V2', '_A C V', 'A C V_',
        'A.C.V', 'A-C-V', 'A C\nV', 'R C V', 'a c v', 'A C V-code',
        'ABC A C V', 'A C V.D',
    )
    for original in ambiguous:
        assert normalize_acronyms(original, ('ACV',)) == (original, [])


def test_normalization_needs_a_matching_standard_word_and_ignores_short_sequences():
    assert normalize_acronyms('A C V', ('FDE',)) == ('A C V', [])
    assert normalize_acronyms('A C V', ()) == ('A C V', [])
    assert normalize_acronyms('A C V', ('A C V', 'acv')) == ('A C V', [])
    assert normalize_acronyms('A I', ('AI',)) == ('A I', [])
    assert normalize_acronyms('A B C D E F G', ('ABCDEFG',)) == ('A B C D E F G', [])


def test_normalization_allows_horizontal_spacing_without_changing_other_text():
    original = 'F\tD\u00a0E，A\u3000C V'
    assert normalize_acronyms(original, ('FDE', 'ACV')) == (
        'FDE，ACV',
        [{'start': 0, 'end': 5, 'before': 'F\tD\u00a0E', 'after': 'FDE'},
         {'start': 6, 'end': 11, 'before': 'A\u3000C V', 'after': 'ACV'}],
    )
