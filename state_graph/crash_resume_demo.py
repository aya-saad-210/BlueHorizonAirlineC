# state_graph/crash_resume_demo.py
#
# Demo evidence for the "checkpointing must survive an actual process
# restart" requirement. This is NOT a unit test -- it's meant to be run
# as two separate OS processes so the kill is real, not simulated:
#
#   Terminal 1:
#     python crash_resume_demo.py start        # prints a run_id, then
#                                                # pauses at await_crew_reply
#
#   Terminal 2 (while terminal 1's process is confirmed dead -- see below):
#     kill -9 <pid of terminal 1's python process>
#     python crash_resume_demo.py resume <run_id>
#
# What to show in the recording (per "Demo evidence" in the brief):
#   1. `start` prints the run_id and its checkpoint history so far
#      (step 0: intake_disruption, step 1: propose_crew, paused at
#      await_crew_reply / waiting_external).
#   2. `kill -9` on that exact PID, proven with `ps` before/after.
#   3. `resume` on a FRESH python process (different PID) reconstructs the
#      run purely from graph_checkpoints -- nothing was kept in memory --
#      and continues from step 1, not step 0. history() at the end shows
#      no duplicate/re-executed steps.

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from crew_reassignment_graph import start_crew_reassignment, submit_crew_reply
from checkpointer import MySQLCheckpointer


def print_history(run_id: str) -> None:
    cp = MySQLCheckpointer()
    print(f"\n--- checkpoint history for {run_id} ---")
    for h in cp.history(run_id):
        print(f"  step {h.step_number}: node={h.node_name} status={h.status}")
    print("--- end history ---\n")


def main():
    if len(sys.argv) < 2:
        print("usage: python crash_resume_demo.py start | resume <run_id>")
        return

    if sys.argv[1] == "start":
        print(f"this process pid = {os.getpid()}  <-- kill -9 this pid to simulate the crash")
        run_id = start_crew_reassignment(flight_number="BH202", started_by="agent_demo")
        print(f"run_id = {run_id}")
        print_history(run_id)
        print("Run is now paused at await_crew_reply (waiting_external). "
              "Kill this process now, then run: python crash_resume_demo.py resume " + run_id)

    elif sys.argv[1] == "resume":
        if len(sys.argv) < 3:
            print("usage: python crash_resume_demo.py resume <run_id>")
            return
        run_id = sys.argv[2]
        print(f"this process pid = {os.getpid()}  <-- a DIFFERENT pid than the killed one")
        print_history(run_id)
        submit_crew_reply(run_id, accepted=True)
        print("submitted crew reply, run continued from its last checkpoint")
        print_history(run_id)

    else:
        print("unknown command:", sys.argv[1])


if __name__ == "__main__":
    main()
