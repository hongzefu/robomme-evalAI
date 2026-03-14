import list_all_submissions as las


def test_normalize_submission_list_accepts_list_payload():
    payload = [{"id": 1}, {"id": 2}]
    assert las.normalize_submission_list(payload) == payload


def test_normalize_submission_list_accepts_results_payload():
    payload = {"count": 1, "results": [{"id": 3}]}
    assert las.normalize_submission_list(payload) == [{"id": 3}]


def test_submission_is_hidden_when_ignored_or_not_public():
    assert las.submission_is_hidden({"ignore_submission": True, "is_public": True})
    assert las.submission_is_hidden({"ignore_submission": False, "is_public": False})
    assert not las.submission_is_hidden({"ignore_submission": False, "is_public": True})


def test_filter_submissions_respects_phase_team_and_hidden_flags():
    submissions = [
        {
            "id": 1,
            "challenge_phase": 10,
            "participant_team_name": "Alpha",
            "ignore_submission": True,
            "is_public": True,
        },
        {
            "id": 2,
            "challenge_phase": 10,
            "participant_team_name": "Beta",
            "ignore_submission": False,
            "is_public": True,
        },
        {
            "id": 3,
            "challenge_phase": 11,
            "participant_team_name": "Alpha",
            "ignore_submission": False,
            "is_public": False,
        },
    ]

    filtered = las.filter_submissions(
        submissions,
        phase_pk=10,
        team_name="Alpha",
        only_hidden=True,
    )

    assert filtered == [submissions[0]]


def test_enrich_submissions_adds_phase_name_and_hidden_flag():
    submissions = [
        {
            "id": 1,
            "challenge_phase": 5297,
            "participant_team_name": "Host Team",
            "ignore_submission": True,
            "is_public": True,
        }
    ]
    phases = [{"id": 5297, "name": "Dev Phase"}]

    enriched = las.enrich_submissions(submissions, phases)

    assert enriched == [
        {
            "id": 1,
            "challenge_phase": 5297,
            "participant_team_name": "Host Team",
            "ignore_submission": True,
            "is_public": True,
            "phase_name": "Dev Phase",
            "hidden": True,
        }
    ]
