# Trojan Insertion Points (recovered by AST analysis)

Spec: **two_block_knock__key_exfil** (trigger=`two_block_knock`, payload=`key_exfil`).

The clean hardened core `rtl/crypto_core_clean.v` is parsed into a structural AST.
Walking that tree pinpoints exactly where the Trojan attaches and why - each point is
an AST node, not a text guess.

## 1. Parameter injection point
- **AST location:** module header parameter list (currently empty)
- **Why here:** Add the Trojan's hard-wired constants without touching the port interface (pin-compatible).

## 2. State injection point
- **AST location:** module item list, just before the sequential always block
- **Why here:** Declare the Trojan control registers; only a couple of flip-flops of overhead.

## 3. Reset branch (AST If, cond `!rst_n`)
- **AST location:** then-block of the reset If
- **Why here:** Clear the Trojan state so it powers up disarmed.

## 4. Trigger observation point (AST If, cond `start && !busy`)
- **AST location:** then-block of the operand-capture branch
- **Why here:** Where each new operand is latched - the natural place to evaluate the trigger.

## 5. Payload multiplexer point (AST Assign `data_out <= cipher_out`)
- **AST location:** result-latch if (cnt == 3'd3) inside the busy branch
- **Why here:** Rewrite the driven value into `fire ? SECRET_KEY : cipher_out`.

## 6. Payload: key exfiltration
- **AST location:** the data_out assignment becomes a 2:1 mux
- **Why here:** the firing block emits SECRET_KEY on MISO instead of the ciphertext.

## 7. Trigger: two-block knock
- **AST location:** then-block of the operand-capture branch if (start && !busy)
- **Why here:** arm on KNOCK_1, fire if the very next encrypt is KNOCK_2 (P=2^-64).

## Result
The transform adds parameters, control flip-flops, trigger logic, and rewrites the
single `data_out` assignment into a multiplexer. No ports, timing, or datapath logic
change, so the Trojaned core is pin- and cycle-identical to the clean core until the
trigger fires (proven by the exploit testbench and the yosys equivalence check).
