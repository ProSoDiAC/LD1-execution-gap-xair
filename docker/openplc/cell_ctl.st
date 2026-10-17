(* Cell controller for OpenPLC (IEC 61131-3 Structured Text), 10 ms scan.
   The PLC owns the line state (1 RUN, 0 PAUSED) and a version (0..32767, wrapping)
   that advances on every change of it. Holding registers (Modbus):
     0 line_state   1 version   2 set_req (operator; -1 = none)
     3 cmd_seq      4 cmd_mode (0 unconditional, 1 version-conditional, 2 predicate: RUN only)   5 cmd_expected
     6 cmd_done     7 cmd_result (1 executed on RUN, 2 executed on PAUSED, 3 refused: version, 4 refused: not RUN)
     8 effects      9 conveyor (1 RUNNING, 0 STOPPED; set by an executed RESUME, cleared by a pause)
   Semantics of the running example: line_state is the line's run mode (set by
   the operator); RESUME restarts the station's conveyor and is admissible only
   in RUN. An executed command on a PAUSED line (cmd_result 2) is a stale
   effect: the conveyor runs on a paused line.
   A command is executed in the scan that sees a new cmd_seq, against the state
   as it is in that scan: check and effect cannot be interleaved with a change.
   A conditional command with cmd_expected outside 0..32767 is always refused.
   The version changes at most once per scan, so it wraps after at least
   32768 scans (about 327 s); a witness can repeat only if an intent lives that
   long, which a deadline below that bound excludes. *)
PROGRAM cell_ctl
  VAR
    line_state AT %QW0 : INT;
    version AT %QW1 : INT;
    set_req AT %QW2 : INT;
    cmd_seq AT %QW3 : INT;
    cmd_mode AT %QW4 : INT;
    cmd_expected AT %QW5 : INT;
    cmd_done AT %QW6 : INT;
    cmd_result AT %QW7 : INT;
    effects AT %QW8 : INT;
    conveyor AT %QW9 : INT;
  END_VAR
  VAR
    booted : BOOL := FALSE;
  END_VAR
  IF NOT booted THEN
    line_state := 1; version := 0; set_req := -1; cmd_seq := 0; cmd_done := 0; cmd_mode := 0; cmd_expected := -1;
    conveyor := 0;
    booted := TRUE;
  END_IF;
  IF set_req >= 0 THEN
    IF set_req <> line_state THEN
      line_state := set_req;
      IF version >= 32767 THEN
        version := 0;
      ELSE
        version := version + 1;
      END_IF;
    END_IF;
    IF set_req = 0 THEN
      conveyor := 0;
    END_IF;
    set_req := -1;
  END_IF;
  IF cmd_seq <> cmd_done THEN
    IF (cmd_mode = 2) AND (line_state <> 1) THEN
      cmd_result := 4;
    ELSIF (cmd_mode = 0) OR (cmd_mode = 2) OR ((cmd_mode = 1) AND (cmd_expected >= 0) AND (cmd_expected = version)) THEN
      effects := effects + 1;
      conveyor := 1;
      IF line_state = 1 THEN
        cmd_result := 1;
      ELSE
        cmd_result := 2;
      END_IF;
    ELSE
      cmd_result := 3;
    END_IF;
    cmd_done := cmd_seq;
  END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#10ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : cell_ctl;
  END_RESOURCE
END_CONFIGURATION
