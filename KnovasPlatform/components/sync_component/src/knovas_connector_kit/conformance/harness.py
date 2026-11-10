"""What a connector package gives the conformance suite: one fresh connector over a source the suite may change.

A harness is any object with these members (a real source may implement them against a sandbox; most packages use a
fake of their source, as the kit's ``example`` does):

- ``connector``: the ``Connector`` under test; ``source``: the source key the suite's jobs name;
- ``credential``: the canary credential the connector was configured with (the suite looks for it everywhere);
- ``sample_steps()``: steps ``{action, target, params}`` valid now, at least one per declared action with exact
  preview, each on its own target; the suite checks the write properties for each staging the steps' actions declare
  (``direct``, ``native_review`` or both);
- ``same_item_steps()`` (optional): two steps of actions declared with exact preview and one staging in common, on
  one item, each changing other fields, or ``None``; without it the suite pairs the first sample step of that staging
  with itself. The suite checks that every change names its own step (D-14), and writes the two together, all or
  none, when both actions declare ``direct`` and ``grouping`` ``atomic`` or ``item`` (D-24); two sample steps of
  ``item`` actions on different targets must be refused in one act;
- ``computed_step()``: a step that would set a field the source computes, or ``None``;
- ``change_behind(target)``: someone else edits the item; ``delete_behind(target)``: the item goes away;
- ``set_cut_listing(on)``: the next reads see an incomplete listing (as a source that cut its answer);
- ``writes(target)``: how many writes reached the source for the item;
- ``crash_after_next_write()``: the next write lands in the source but the connector never finishes the job (a
  ``direct`` write; a repeated staging ``act`` is checked by delivering it twice);
- ``fail_next_call_with_secret()``: the next source call fails with an error whose text quotes the credential;
- ``alter_next_write()``: the source stores something other than what was written (a trigger, a normalisation);
- ``review(stage_ref, accept)``: the source's own reviewer accepts or declines a staged change; accepting applies it
  (with ``alter_next_write()`` set before, the source stores something else then).
"""
