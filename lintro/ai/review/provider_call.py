"""The one provider-call seam for the built-in review passes.

Every built-in review pass — the per-chunk review in
:mod:`lintro.ai.review.response_pipeline`, the depth-2 question generator in
:mod:`lintro.ai.review.checklist_pass`, and the depth-3 sweep in
:mod:`lintro.ai.review.adversarial_pass` — issues its provider call through
this module rather than binding :func:`lintro.ai.invoke.call_ai` in its own
namespace. Because the name is resolved on this module at call time, a test
that replaces ``lintro.ai.review.provider_call.call_ai`` sees *every* call the
review makes, whatever the depth. That is the documented hook: patch it, and
nothing below reaches a real provider.

Import the module, never the function::

    from lintro.ai.review import provider_call

    response = await provider_call.call_ai(...)

``from lintro.ai.review.provider_call import call_ai`` would rebind the name in
the importing module and reopen the per-module seam this exists to close.

The custom-agent runner (:mod:`lintro.ai.review.custom_agent_runner`) and the
cross-chunk synthesis pass (:mod:`lintro.ai.review.synthesis`) are separate,
independently stubbed passes and keep their own seams.
"""

from __future__ import annotations

from lintro.ai.invoke import call_ai

__all__ = ["call_ai"]
