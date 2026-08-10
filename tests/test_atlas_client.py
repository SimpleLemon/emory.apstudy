import unittest

from services.atlas_client import (
    _filter_sections_result,
    get_sections_index,
    get_starred_general_ed_requirements,
    is_section_trackable,
    search_courses,
)


class AtlasClientTests(unittest.TestCase):
    def test_zero_seat_sections_are_trackable_even_with_status_mismatch(self):
        self.assertTrue(is_section_trackable({
            "enrollment_status": "Closed",
            "seats_available": 0,
        }))
        self.assertTrue(is_section_trackable({
            "enrollment_status": "Open",
            "seats_available": "0 seats",
        }))
        self.assertTrue(is_section_trackable({
            "enrollment_status": "Waitlist",
            "seats_available": "0",
        }))

    def test_positive_seat_sections_are_not_trackable(self):
        self.assertFalse(is_section_trackable({
            "enrollment_status": "Closed",
            "seats_available": 1,
        }))
        self.assertFalse(is_section_trackable({
            "enrollment_status": "Open",
            "seats_available": 3,
        }))

    def test_closed_sections_without_explicit_seats_are_trackable(self):
        self.assertTrue(is_section_trackable({
            "enrollment_status": "Closed",
            "seats_available": None,
        }))

    def test_cancelled_sections_are_not_trackable(self):
        self.assertFalse(is_section_trackable({
            "enrollment_status": "Closed",
            "seats_available": 0,
            "is_cancelled": True,
        }))

    def test_section_search_exact_code_and_no_space_query(self):
        spaced = get_sections_index(term="Fall_2026", include_cancelled=False, query="CHEM 150", limit=10)
        compact = get_sections_index(term="Fall_2026", include_cancelled=False, query="chem150", limit=10)

        self.assertGreater(spaced["total"], 0)
        self.assertGreater(compact["total"], 0)
        self.assertEqual(spaced["sections"][0]["course_code"], "CHEM 150")
        self.assertEqual(compact["sections"][0]["course_code"], "CHEM 150")

    def test_section_search_crn_title_instructor_and_limit(self):
        chem = get_sections_index(term="Fall_2026", include_cancelled=False, query="CHEM 150", limit=1)
        self.assertEqual(chem["count"], 1)
        crn = chem["sections"][0]["crn"]
        if crn:
            by_crn = get_sections_index(term="Fall_2026", include_cancelled=False, query=crn, limit=5)
            self.assertEqual(by_crn["sections"][0]["crn"], crn)

        title = get_sections_index(term="Fall_2026", include_cancelled=False, query="structure and properties", limit=10)
        self.assertGreater(title["total"], 0)
        self.assertTrue(any("CHEM 150" == section["course_code"] for section in title["sections"]))

    def test_cancelled_filter_excludes_cancelled_sections(self):
        result = get_sections_index(term="Fall_2026", include_cancelled=False, query="CHEM", limit=50)
        self.assertTrue(result["sections"])
        self.assertFalse(any(section["is_cancelled"] for section in result["sections"]))

    def test_onboarding_search_uses_indexed_results(self):
        result = search_courses("CHEM 150", term="Fall_2026")
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["results"][0]["course_code"], "CHEM 150")

    def test_campus_filter_treats_existing_unknown_corpus_as_atlanta(self):
        from services.atlas_client import invalidate_cache
        invalidate_cache()
        atlanta = get_sections_index(term="Fall_2026", include_cancelled=False, query="CHEM 150", campus="atlanta", limit=20)
        oxford = get_sections_index(term="Fall_2026", include_cancelled=False, query="CHEM 150", campus="oxford", limit=20)

        chem_atlanta = [section for section in atlanta["sections"] if section["course_code"] == "CHEM 150"]
        chem_oxford = [section for section in oxford["sections"] if section["course_code"] == "CHEM 150"]

        self.assertGreater(len(chem_atlanta), 0)
        self.assertEqual(len(chem_oxford), 0)
        self.assertTrue(all(section.get("campus") == "Atlanta" for section in chem_atlanta))

    def test_synthetic_oxford_and_starred_requirement_filters(self):
        result = {
            "term": "Fall_2026",
            "terms": ["Fall_2026"],
            "sections": [
                {"course_code": "CS 170", "subject": "CS", "catalog_number": "170", "section_number": "1", "crn": "1", "campus": "Atlanta", "requirement_designation": "QR"},
                {"course_code": "OXBI 141", "subject": "OXBI", "catalog_number": "141", "section_number": "1", "crn": "2", "campus": "Oxford", "requirement_designation": "NS*"},
            ],
            "count": 2,
        }

        oxford = _filter_sections_result(result, campus="oxford", requirement="starred")

        self.assertEqual(oxford["total"], 1)
        self.assertEqual(oxford["sections"][0]["course_code"], "OXBI 141")

    def test_enrollment_status_filter_accepts_multiple_normalized_statuses(self):
        result = {
            "term": "Fall_2026",
            "terms": ["Fall_2026"],
            "sections": [
                {"course_code": "BIO 101", "enrollment_status": "Open"},
                {"course_code": "CHEM 150", "enrollment_status": "Closed"},
                {"course_code": "PHYS 141", "enrollment_status": "W"},
            ],
            "count": 3,
        }

        filtered = _filter_sections_result(result, statuses=["open", "Waitlist"])

        self.assertEqual(filtered["total"], 2)
        self.assertEqual(
            [section["course_code"] for section in filtered["sections"]],
            ["BIO 101", "PHYS 141"],
        )

    def test_exact_starred_general_ed_requirement_filter(self):
        requirement = "First Year Writing(*)"
        self.assertIn(requirement, get_starred_general_ed_requirements())
        result = {
            "term": "Fall_2026",
            "terms": ["Fall_2026"],
            "sections": [
                {"course_code": "ENG 101", "subject": "ENG", "catalog_number": "101", "section_number": "1", "crn": "1", "requirements": [requirement]},
                {"course_code": "ENG_OX 185", "subject": "ENG_OX", "catalog_number": "185", "section_number": "1", "crn": "3", "requirements": ["First-Year Writing(*)"]},
                {"course_code": "ENG 181", "subject": "ENG", "catalog_number": "181", "section_number": "1", "crn": "2", "requirements": ["First Year Seminar(*)"]},
            ],
            "count": 3,
        }

        filtered = _filter_sections_result(result, requirement=requirement)

        self.assertEqual(filtered["total"], 2)
        self.assertEqual({section["course_code"] for section in filtered["sections"]}, {"ENG 101", "ENG_OX 185"})

    def test_general_ed_alias_and_composite_filters(self):
        result = {
            "term": "Spring_2026",
            "terms": ["Spring_2026"],
            "sections": [
                {
                    "course_code": "NBB 220",
                    "subject": "NBB",
                    "catalog_number": "220",
                    "section_number": "1",
                    "crn": "1",
                    "requirements": ["NS", "Race and Ethnicity(*)"],
                },
                {
                    "course_code": "HLTH 200",
                    "subject": "HLTH",
                    "catalog_number": "200",
                    "section_number": "1",
                    "crn": "2",
                    "requirements": [
                        "Quantitative Reasoning(*)",
                        "Continuing Comm.& Writing with Race & Ethnicity(*)",
                    ],
                },
                {
                    "course_code": "ENG 190",
                    "subject": "ENG",
                    "catalog_number": "190",
                    "section_number": "1",
                    "crn": "3",
                    "requirements": ["FS", "First Year Seminar"],
                },
            ],
            "count": 3,
        }

        ns_ethn = _filter_sections_result(result, requirement="Natural Sciences with ETHN(*)")
        qr_ethn = _filter_sections_result(result, requirement="Quantitat.Reasoning w.ETHN(*)")
        first_year_seminar = _filter_sections_result(result, requirement="First Year Seminar(*)")

        self.assertEqual(ns_ethn["total"], 1)
        self.assertEqual(ns_ethn["sections"][0]["course_code"], "NBB 220")
        self.assertEqual(qr_ethn["total"], 1)
        self.assertEqual(qr_ethn["sections"][0]["course_code"], "HLTH 200")
        self.assertEqual(first_year_seminar["total"], 1)
        self.assertEqual(first_year_seminar["sections"][0]["course_code"], "ENG 190")

    def test_synthetic_eng_ox_oxford_and_starred_filters(self):
        result = {
            "term": "Fall_2026",
            "terms": ["Fall_2026"],
            "sections": [
                {
                    "course_code": "CHEM 150",
                    "subject": "CHEM",
                    "catalog_number": "150",
                    "section_number": "1",
                    "crn": "2760",
                    "campus": "Atlanta",
                    "campus_description": "ATL@ATLANTA",
                    "requirement_designation": "Natural Sciences(*)",
                    "seats_available": 34,
                    "enrollment_capacity": 36,
                },
                {
                    "course_code": "ENG_OX 185",
                    "subject": "ENG_OX",
                    "catalog_number": "185",
                    "section_number": "1",
                    "crn": "4196",
                    "campus": "Oxford",
                    "campus_description": "OXF@OXFORD",
                    "requirement_designation": "First-Year Writing(*)",
                    "seats_available": 16,
                    "enrollment_capacity": 16,
                },
            ],
            "count": 2,
        }

        oxford = _filter_sections_result(result, campus="oxford", requirement="starred")
        self.assertEqual(oxford["total"], 1)
        self.assertEqual(oxford["sections"][0]["course_code"], "ENG_OX 185")

        atlanta = _filter_sections_result(result, campus="atlanta", query="CHEM 150")
        self.assertEqual(atlanta["total"], 1)
        self.assertEqual(atlanta["sections"][0]["seats_available"], 34)

    def test_enriched_chem_150_corpus_when_present(self):
        chem = get_sections_index(term="Fall_2026", include_cancelled=False, query="2760", limit=1)
        if not chem.get("sections"):
            self.skipTest("CHEM 150 corpus not enriched yet")
        section = chem["sections"][0]
        if section.get("seats_available") is None:
            self.skipTest("CHEM 150 corpus missing seat enrichment")
        self.assertEqual(section["crn"], "2760")
        self.assertEqual(section.get("campus"), "Atlanta")
        self.assertEqual(section.get("enrollment_capacity"), 36)
        self.assertEqual(section.get("grading_mode"), "Student Option")
        self.assertEqual(section.get("instruction_method"), "In Person")


if __name__ == "__main__":
    unittest.main()
