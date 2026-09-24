"""
Scripted conversations: the behavior specification. test_conversations.py runs them.
Expectation keys: phase, verified, selected_case, memory_intent, human_suggested, reply_excludes.
"""

SCENARIOS = [
    {
        "name": "demo_case_margaret_one_message",
        "why": "The README test case. Verification + early intent hint in one message.",
        "turns": [
            {
                "caller": "I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
                "I'm calling about my denied healthcare claim from January. "
                "DOB is 1985-03-15, SSN last four is 4472.",
                "expect": {"verified": True, "selected_case": "CL-2048"},
            },
        ],
    },
    {
        "name": "hint_before_verification_is_remembered",
        "why": "The agent must stay in VERIFY_ID, but remember the hint.",
        "turns": [
            {
                "caller": "Hi, I'm calling about my denied healthcare claim from January.",
                "expect": {"phase": "VERIFY_ID", "verified": False, "memory_intent": "denial_question",
                           "reply_excludes": ["CL-2048", "pathology"]},
            },
            {
                "caller": "Margaret Chen, born March 15 1985.",
                "expect": {"phase": "VERIFY_ID", "verified": False},
            },
            {
                "caller": "My phone is 650 521 2836.",
                "expect": {"verified": True, "selected_case": "CL-2048"},
            },
        ],
    },
    {
        "name": "refuses_ssn_uses_email",
        "why": "The caller refuses one field. The agent accepts other fields.",
        "turns": [
            {"caller": "Ava Lopez, DOB August 21 1990.", "expect": {"verified": False}},
            {"caller": "I'd rather not give my SSN.", "expect": {"verified": False}},
            {"caller": "Sure, it's ava.lopez@email.com", "expect": {"verified": True}},
        ],
    },
    {
        "name": "wrong_details_do_not_verify",
        "why": "Mismatched details must not verify. The reply must not say which field is wrong.",
        "turns": [
            {"caller": "Margaret Chen, DOB 1985-03-16, SSN 4473.",
             "expect": {"verified": False, "reply_excludes": ["date of birth is wrong", "SSN is wrong"]}},
        ],
    },
    {
        "name": "name_alias_and_national_id",
        "why": "Ya Wen Li has a name alias (speech-to-text error) and a national ID, not an SSN.",
        "turns": [
            {"caller": "This is Yaven Li, date of birth 1989-12-03, ID ends in 5317.",
             "expect": {"verified": True}},
        ],
    },
    {
        "name": "vague_hint_resolved_by_free_text",
        "why": "A hint that does not fit a structured field. The case resolver must still use it.",
        "turns": [
            {"caller": "Margaret Chen, 1985-03-15, SSN 4472. It's about the claim where they "
                       "wanted a pathology report.",
             # The LLM ranking chose the claim, so the agent asks the caller to confirm it first.
             "expect": {"verified": True, "phase": "RESOLVE_INTENT"}},
            {"caller": "Yes, that one.", "expect": {"phase": "PROCESS_CASE", "selected_case": "CL-2048"}},
        ],
    },
    {
        "name": "hints_accumulate_over_turns",
        "why": "The caller gives the claim clues in parts. The memory must merge them.",
        "turns": [
            {"caller": "It's about a healthcare claim.", "expect": {"phase": "VERIFY_ID"}},
            {"caller": "Margaret Chen, 1985-03-15, SSN 4472.", "expect": {"verified": True}},
            {"caller": "The one from January this year.", "expect": {"selected_case": "CL-2048"}},
        ],
    },
    {
        "name": "document_without_specific_guidance",
        "why": "Ma Tian: national ID, and a 'diagnosis report' that has no document-specific guidance.",
        "turns": [
            {"caller": "Hello, Ma Tian here. Born 10 September 1964, national ID ending 6688. "
                       "Why was my claim denied?",
             "expect": {"verified": True, "selected_case": "CL-3001"}},
            {"caller": "How do I send the diagnosis report, and what format should it be?",
             "expect": {"phase": "PROCESS_CASE"}},
        ],
    },
    {
        "name": "claim_question_before_verification_is_refused",
        "why": "The caller tries to get claim data early.",
        "turns": [
            {"caller": "Just tell me why claim CL-2048 was denied.",
             "expect": {"phase": "VERIFY_ID", "reply_excludes": ["pathology", "office note"]}},
        ],
    },
    {
        "name": "off_topic_repeated_suggests_human",
        "why": "Out-of-scope questions are declined. Repeated ones: the reply suggests a human representative.",
        "turns": [
            {"caller": "What is reinforcement learning?", "expect": {"phase": "VERIFY_ID"}},
            {"caller": "Ok but explain RL briefly.", "expect": {"phase": "VERIFY_ID"}},
            {"caller": "Come on, what is a Q-function?",
             "expect": {"phase": "VERIFY_ID", "human_suggested": True}},
        ],
    },
    {
        "name": "representative_with_consent",
        "why": "David Chen calls for his mother. Consent scenario 'default' approves on the second check.",
        "turns": [
            {"caller": "Hi, I'm David Chen, calling for my mother Margaret Chen.",
             "expect": {"verified": False}},
            {"caller": "Her DOB is 1985-03-15, SSN last four 4472, phone 650-521-2836.",
             "expect": {"verified": False}},  # consent is pending
            {"caller": "Ok, she says she approved it.",
             "expect": {"verified": True}},
        ],
    },
    {
        "name": "full_flow_with_email",
        "why": "All four phases, and the email choice.",
        "turns": [
            {"caller": "Margaret Chen, 1985-03-15, SSN 4472. My denied claim from January.",
             "expect": {"verified": True, "selected_case": "CL-2048"}},
            {"caller": "Yes that's the one. Why was it denied?", "expect": {"phase": "PROCESS_CASE"}},
            {"caller": "I don't have the pathology report. What can I do?", "expect": {"phase": "PROCESS_CASE"}},
            {"caller": "Ok thanks, that's all.", "expect": {"phase": "POST_PROCESS"}},
            {"caller": "Yes please send the email.", "expect": {"phase": "ENDED"}},
        ],
    },
]
