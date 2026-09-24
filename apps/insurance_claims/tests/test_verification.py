"""
Unit tests for identity verification. No LLM. Run: .venv/bin/pytest
"""

import pytest

from sop_agent.config import Settings
from sop_agent.data_store import DataStore
from sop_agent.state import IdentityClaims
from sop_agent.verification import VerificationStatus, verify_identity


@pytest.fixture(scope="module")
def store():
    return DataStore(Settings().fixtures_dir)


def test_three_matching_fields_verify(store):
    """Name + DOB + SSN last 4 of Margaret Chen -> VERIFIED, party_id "P9"."""
    claims = IdentityClaims(full_name="Margaret Chen", dob="1985-03-15", id_last4="4472")
    result = verify_identity(claims, store)
    assert result.status == VerificationStatus.VERIFIED
    assert result.party_id == "P9"


def test_two_fields_need_more(store):
    """Name + DOB only -> NEED_MORE, still_needed == 1."""
    result = verify_identity(IdentityClaims(full_name="Margaret Chen", dob="1985-03-15"), store)
    assert result.status == VerificationStatus.NEED_MORE
    assert result.still_needed == 1
    assert result.party_id is None


@pytest.mark.parametrize("phone", ["(650) 521-2836", "650.521.2836", "+1 650 521 2836", "6505212836"])
def test_phone_formats_match(store, phone):
    """All the usual phone formats match "+16505212836"."""
    claims = IdentityClaims(full_name="Margaret Chen", dob="1985-03-15", phone=phone)
    assert verify_identity(claims, store).status == VerificationStatus.VERIFIED


def test_name_alias_matches(store):
    """"Yaven Li" matches the record of Ya Wen Li (name_aliases)."""
    claims = IdentityClaims(full_name="Yaven Li", dob="1989-12-03", id_last4="5317")
    result = verify_identity(claims, store)
    assert result.status == VerificationStatus.VERIFIED
    assert result.party_id == "P13"


def test_email_alias_matches(store):
    """The alias email of Ya Wen Li also counts."""
    claims = IdentityClaims(full_name="Ya Wen Li", dob="1989-12-03", email="YaWen.Li@example.com")
    assert verify_identity(claims, store).party_id == "P13"


def test_national_id_counts_as_last4(store):
    """Ma Tian: the national ID last 4 ("6688") counts as the ID field."""
    claims = IdentityClaims(full_name="Ma Tian", dob="1964-09-10", id_last4="6688")
    assert verify_identity(claims, store).party_id == "P12"


def test_name_word_order_does_not_matter(store):
    claims = IdentityClaims(full_name="Chen, Margaret", dob="1985-03-15", id_last4="4472")
    assert verify_identity(claims, store).status == VerificationStatus.VERIFIED


def test_wrong_values_do_not_verify(store):
    """Correct name + wrong DOB + wrong SSN -> not VERIFIED. The caller can still give phone and email."""
    claims = IdentityClaims(full_name="Margaret Chen", dob="1985-03-16", id_last4="4473")
    result = verify_identity(claims, store)
    assert result.status == VerificationStatus.NEED_MORE
    assert result.party_id is None


def test_two_of_three_asks_for_one_more(store):
    """Three fields, two match -> ask for one more field (not a failure)."""
    claims = IdentityClaims(full_name="Margaret Chen", dob="1985-03-15", id_last4="0000")
    result = verify_identity(claims, store)
    assert result.status == VerificationStatus.NEED_MORE
    assert result.still_needed == 1


def test_mismatch_when_no_fields_remain(store):
    """All five fields given, only two match -> MISMATCH."""
    claims = IdentityClaims(
        full_name="Margaret Chen", dob="1985-03-15", id_last4="0000",
        phone="+10000000000", email="nobody@example.com",
    )
    assert verify_identity(claims, store).status == VerificationStatus.MISMATCH


def test_refused_fields_reduce_the_options(store):
    """Two fields given, both wrong, and the caller refuses two others -> MISMATCH."""
    claims = IdentityClaims(full_name="Nobody Here", dob="2000-01-01")
    result = verify_identity(claims, store, refused=["id_last4", "phone"])
    assert result.status == VerificationStatus.MISMATCH


def test_policy_number_does_not_count(store):
    """Name + policy number + DOB -> NEED_MORE (policy number is not a counted field)."""
    claims = IdentityClaims(full_name="Margaret Chen", dob="1985-03-15", policy_number="POL-9921")
    assert verify_identity(claims, store).status == VerificationStatus.NEED_MORE


def test_verified_claims_come_only_from_the_owner(store):
    """The data store refuses a claim of another customer."""
    assert store.get_claim("CL-3001", party_id="P9") is None
    assert store.get_claim("cl-2048", party_id="P9").case_id == "CL-2048"
