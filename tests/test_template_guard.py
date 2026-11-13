"""Template guard: every bad LLM template must be rejected with a clear message."""

from __future__ import annotations

import pytest

from custom_components.assist_learner.commands.template_guard import validate_template

ENT = {"sensor.outdoor_temperature", "sensor.humidity"}


def test_good_template_passes_and_collects_entities():
    r = validate_template(
        "It's {{ states('sensor.outdoor_temperature') | round(0) }} degrees, "
        "humidity {{ states('sensor.humidity') | int }} percent.",
        ENT,
    )
    assert r.ok and r.entities == ENT


def test_slots_allowed():
    assert validate_template("Set to {{ slots.level }} percent", set(), {"level"}).ok
    assert not validate_template("Set to {{ slots.nope }}", set(), {"level"}).ok


@pytest.mark.parametrize(
    "tpl, fragment",
    [
        ("{{ states('sensor.other') }}", "not in the command's entity list"),
        ("{{ states(var) }}", "literal entity id"),
        ("{{ states(\"sensor.\" ~ 'x') }}", "literal entity id"),
        ("{{ now() }}", "may be called"),
        ("{{ states.sensor.outdoor_temperature.state }}", "attribute access"),
        ("{% for s in states %}{{ s }}{% endfor %}", "For"),
        ("{% set x = 1 %}{{ x }}", "Assign"),
        ("{{ range(5) }}", "may be called"),
        ("{{ hass.services.call('light','turn_on') }}", "may be called"),
        ("{{ 'x' | map('attr', 'y') }}", "filter 'map'"),
        ("{{ states('sensor.humidity') | from_json }}", "filter 'from_json'"),
        ("{{ ''.__class__ }}", "attribute access"),
        ("{% macro m() %}x{% endmacro %}", "Macro"),
        ("{% include 'x' %}", "Include"),
        ("", "empty"),
        ("{{ unclosed", "syntax"),
    ],
)
def test_bad_templates_rejected(tpl, fragment):
    r = validate_template(tpl, ENT)
    assert not r.ok
    assert any(fragment in e for e in r.errors), r.errors
