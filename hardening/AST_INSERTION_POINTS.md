# Trojan Insertion Points (recovered by AST analysis)

The clean hardened core `rtl/crypto_core_clean.v` was parsed into a structural
AST by `hardening/ast_insertion.py`. Walking that tree pinpoints exactly where the
hardware Trojan attaches and why. Each point below is an AST node, not a text guess.

## 1. Parameter injection point
- **AST location:** module header parameter list (currently empty)
- **Why here:** Add hard-wired Trojan constants (TROJAN_EN, SECRET_KEY, KNOCK_1, KNOCK_2) without touching the port interface, so the module remains pin-compatible.

## 2. State injection point
- **AST location:** module item list, just before the sequential `always @(posedge clk)` block
- **Why here:** Declare the 2-bit Trojan FSM registers (armed, fire); ~2 flip-flops of overhead.

## 3. Reset branch (AST If, cond `!rst_n`)
- **AST location:** then-block of the reset If inside the sequential always
- **Why here:** Clear armed/fire on reset so the Trojan powers up disarmed.

## 4. Trigger observation point (AST If, cond `start && !busy`)
- **AST location:** then-block of the operand-capture branch (start && !busy)
- **Why here:** This is where each new operand is latched - the natural place to watch for the two-block knock and set armed/fire, in ENCRYPT mode only.

## 5. Payload multiplexer point (AST Assign `data_out <= cipher_out`)
- **AST location:** result-latch `if (cnt == 3'd3)` inside the busy branch
- **Why here:** Replace the driven value with `fire ? SECRET_KEY : cipher_out`, so exactly one block (the firing knock) emits the key while every other block is untouched.

## Result
The transformation adds 4 parameters, 2 flip-flops (`armed`, `fire`), one trigger
`if` node in the operand-capture branch, and rewrites the single `data_out`
assignment into a 2:1 multiplexer. No ports, timing, or datapath logic change, so
the Trojaned core is pin- and cycle-identical to the clean core until the knock fires.
