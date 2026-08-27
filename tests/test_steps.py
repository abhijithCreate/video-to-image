"""The three-step page structure, asserted on the rendered HTML.

These are cheap guards for things that are easy to lose in an edit: the step
sections, their back arrows, and the counters that orient the user.
"""

from __future__ import annotations

import re


def test_all_three_steps_are_rendered(client):
    body = client.get("/").text
    for step in ("upload", "configure", "results"):
        assert f'data-step="{step}"' in body


def test_only_the_first_step_starts_visible(client):
    body = client.get("/").text
    upload = re.search(r'<section id="step-upload"[^>]*>', body).group(0)
    assert "hidden" not in upload
    for step in ("configure", "results"):
        section = re.search(rf'<section id="step-{step}"[^>]*>', body).group(0)
        assert "hidden" in section, f"step {step} must start hidden"


def test_each_later_step_has_a_labelled_back_arrow(client):
    body = client.get("/").text
    assert body.count('class="back-arrow"') == 2
    assert 'aria-label="Back to upload"' in body
    assert 'aria-label="Back to options"' in body


def test_every_step_states_where_you_are(client):
    body = client.get("/").text
    for label in ("Step 1 of 3", "Step 2 of 3", "Step 3 of 3"):
        assert label in body, f"missing counter: {label}"


def test_no_navigation_exposes_the_other_steps(client):
    body = client.get("/").text
    assert "step-chip" not in body
    assert 'aria-label="Progress"' not in body


def test_back_arrows_point_at_the_previous_step(client):
    body = client.get("/").text
    configure = body[body.index('id="step-configure"'):body.index('id="step-results"')]
    assert 'data-goto="upload"' in configure
    results = body[body.index('id="step-results"'):]
    assert 'data-goto="configure"' in results
