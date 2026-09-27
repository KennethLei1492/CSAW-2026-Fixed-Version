#!/usr/bin/env python3
# ============================================================================
#  characterize.py - Stage 1 output: pin_map.json + cipher_characterization.json
#
#  Drives the recovered netlist as a race-free oracle (reusing extract_cipher's
#  harness) to (a) confirm the recovered pin map by reproducing the published
#  golden vector, and (b) characterize the cipher: block size, encrypt/decrypt
#  inverse, and byte-locality (no diffusion). Writes the two JSON artifacts the
#  pipeline's Stage 1 is required to produce.
# ============================================================================
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_cipher as ec

BUILD = os.path.join(HERE, "..", "build")

PIN_MAP = {
    "SCK": "io_9_31_1", "RST_N": "io_13_31_1", "MOSI": "io_16_31_0",
    "MISO": "io_16_31_1", "NORM_CS_N": "io_17_31_0", "ENC_DEC": "io_19_31_1",
    "START": "io_18_31_1", "BUSY": "io_8_31_1", "ICE_LED": "io_19_31_0",
}

def main():
    os.makedirs(BUILD, exist_ok=True)
    ec.build()
    golden_ct = ec.op(0x59C359C3, 0)
    reproduced = (golden_ct == 0x9CD84392)

    # byte-locality: flip each input byte, record which output bytes react
    base = ec.op(0, 0)
    locality = {}
    for ib in range(4):
        changed = set()
        for delta in (0x01, 0x80, 0xFF):
            y = ec.op((delta << (8*ib)), 0)
            for ob in range(4):
                if ((y >> (8*ob)) & 0xff) != ((base >> (8*ob)) & 0xff):
                    changed.add(ob)
        locality["input_byte_%d" % ib] = sorted(changed)
    byte_local = all(locality["input_byte_%d" % i] == [i] for i in range(4))

    # encrypt/decrypt are inverses (spot check)
    inv_ok = all(ec.op(ec.op(v, 0), 1) == v for v in (0x59C359C3, 0x12345678, 0xFFFFFFFF))

    json.dump(PIN_MAP, open(os.path.join(BUILD, "pin_map.json"), "w"), indent=2)
    char = {
        "block_bits": 32,
        "key": "hard-wired in the bitstream (no key is sent over SPI)",
        "modes": {"encrypt": "ENC_DEC=0", "decrypt": "ENC_DEC=1"},
        "busy_cycles": 4,
        "golden_vector": {"plaintext": "0x59C359C3",
                          "ciphertext": "0x%08X" % golden_ct,
                          "expected": "0x9CD84392",
                          "reproduced": reproduced},
        "byte_local": byte_local,
        "byte_locality_map": locality,
        "encrypt_decrypt_inverse": inv_ok,
    }
    json.dump(char, open(os.path.join(BUILD, "cipher_characterization.json"), "w"), indent=2)
    print("characterize.py: golden vector reproduced=%s ; byte_local=%s ; enc/dec inverse=%s" %
          (reproduced, byte_local, inv_ok))
    print("  -> build/pin_map.json, build/cipher_characterization.json")
    return 0 if (reproduced and byte_local and inv_ok) else 1

if __name__ == "__main__":
    sys.exit(main())
