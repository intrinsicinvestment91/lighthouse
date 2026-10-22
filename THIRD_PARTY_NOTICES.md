# Third-party notices

Lighthouse's own licence is in [`LICENSE`](LICENSE). This file records third-party code used in this
repository and the terms it is used under.

## BIP-340 reference implementation — `modules/_schnorr.py`

- **Source:** bitcoin/bips, `bip-0340/reference.py` at commit
  `130b8e0fa7f30fa45532f080b87b8db27a79e46e` (file last changed in commit
  `f98774a68c23269d9bfa78c8d9c4671ebf4ceb69`):
  https://github.com/bitcoin/bips/blob/130b8e0fa7f30fa45532f080b87b8db27a79e46e/bip-0340/reference.py
- **Relationship:** `modules/_schnorr.py` is **probably adapted from** this file; it is not proven to
  be a copy.
- **Upstream terms** (`bip-0340/LICENSE`): `BSD-2-Clause OR MIT OR CC0-1.0` — "The contents of this
  directory are provided under one of the following sets of terms, at your choice:" BSD-2-Clause, MIT,
  or CC0 1.0. **Used here under the MIT License.**
- **Upstream authors** (BIP-340 header): Pieter Wuille, Jonas Nick, Tim Ruffing. Neither the upstream
  file nor its LICENSE carries a copyright line, so none is reproduced here.
- **Status:** BIP-340 states that the reference implementation "is for demonstration purposes only and
  not to be used in production environments". In Lighthouse, `modules/_schnorr.py` is a demonstration
  and test helper, not for production keys.

MIT License (https://opensource.org/license/MIT), permission notice:

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
