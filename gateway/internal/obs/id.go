package obs

import (
	"crypto/rand"
	"time"
)

// crockford is Crockford's base32 alphabet: no I, L, O, or U, so a trace id read
// aloud from a support ticket survives the trip.
const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// NewTraceID returns a ULID: 48 bits of millisecond timestamp followed by 80 bits
// of randomness, Crockford-base32 encoded to 26 characters. Lexicographic order
// matches time order, which makes log greps behave.
func NewTraceID() string {
	var b [16]byte
	ms := uint64(time.Now().UnixMilli())
	for i := 0; i < 6; i++ {
		b[i] = byte(ms >> (40 - 8*uint(i)))
	}
	// crypto/rand.Read never returns an error on any supported platform.
	_, _ = rand.Read(b[6:])

	// 26 chars * 5 bits = 130 bits, so the value is left-padded by two zero bits.
	out := make([]byte, 26)
	for i := 0; i < 26; i++ {
		var v byte
		for j := 0; j < 5; j++ {
			v = v<<1 | bitAt(b[:], i*5+j-2)
		}
		out[i] = crockford[v]
	}
	return string(out)
}

func bitAt(b []byte, i int) byte {
	if i < 0 || i >= len(b)*8 {
		return 0
	}
	return (b[i/8] >> (7 - uint(i%8))) & 1
}
