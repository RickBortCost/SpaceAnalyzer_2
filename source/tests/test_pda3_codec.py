import itertools,random,struct,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pda3.reader import PDA3Reader
from pda3_fixture.rle_codec import encode_exact

def decode(payload):
    header=bytearray(80);header[:4]=b'PDA3';struct.pack_into('<I',header,8,64)
    struct.pack_into('<QQ',header,40,10,6000000010);struct.pack_into('<Q',header,16,84+len(payload))
    reader=PDA3Reader(bytes(header)+struct.pack('<I',len(payload))+payload+struct.pack('<QQ',0,1)+b'i'*16)
    return reader.block_bits(reader.blocks[0])

def oracle(words):
    budgets=[set() for _ in range(len(words)+1)];budgets[0].add(0)
    for i in range(len(words)):
        for n in range(1,min(127,len(words)-i)+1):
            for b in budgets[i]:budgets[i+n].add(b+4*n+1)
            if n<=126 and all(w==words[i] for w in words[i:i+n]):
                for b in budgets[i]:budgets[i+n].add(b+5);budgets[i+n].add(b+7)
    return budgets[-1]

class Codec(unittest.TestCase):
    def test_small_exhaustive_bit_exact_and_representable_budgets(self):
        words=[struct.pack('<f',v) for v in (-0.,1.)]
        for n in range(1,6):
            for sequence in itertools.product(words,repeat=n):
                raw=b''.join(sequence);expected=oracle(sequence)
                for budget in range(1,5*n+1):
                    if budget in expected:
                        try:encoded=encode_exact(raw,budget)
                        except ValueError as e:self.fail(f'{n} {sequence} budget {budget}: {e}')
                        self.assertEqual(len(encoded),budget);self.assertEqual(decode(encoded),raw)
                    else:
                        with self.assertRaises(ValueError):encode_exact(raw,budget)

    def test_random_uncompressible_refused_instead_of_approximated(self):
        random.seed(51);raw=b''.join(struct.pack('<f',random.random()) for _ in range(1450))
        with self.assertRaises(ValueError):encode_exact(raw,5000)
        self.assertEqual(decode(encode_exact(raw,6000)),raw)

    def test_long_constant_and_steps_exact_fixed_capacity(self):
        for n in (1289,1320,1450,60000):
            raw=struct.pack('<f',-0.)*n
            self.assertEqual(decode(encode_exact(raw,4998)),raw)
        raw=b''.join(struct.pack('<f',(i//40)%7) for i in range(1450))
        self.assertEqual(decode(encode_exact(raw,4999)),raw)

if __name__=='__main__':unittest.main(verbosity=2)
