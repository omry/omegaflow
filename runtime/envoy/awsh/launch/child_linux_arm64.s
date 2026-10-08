#include "textflag.h"

TEXT ·forkChild(SB),NOSPLIT,$96-24
	MOVD args+0(FP), R19
	MOVD $-1, R4
	MOVD R4, 8(RSP)
	MOVD $135, R8 // rt_sigprocmask
	MOVD $2, R0
	ADD $8, RSP, R1
	ADD $16, RSP, R2
	MOVD $8, R3
	SVC
	CMP $0, R0
	BLT mask_error
	MOVD $220, R8 // clone(SIGCHLD), separate address space
	MOVD $17, R0
	MOVD ZR, R1
	MOVD ZR, R2
	MOVD ZR, R3
	MOVD ZR, R4
	SVC
	CBZ R0, child
	MOVD R0, R23
	MOVD $135, R8
	MOVD $2, R0
	ADD $16, RSP, R1
	MOVD ZR, R2
	MOVD $8, R3
	SVC
	MOVD R23, R0
	CMP $0, R0
	BLT mask_error
	MOVD R0, pid+8(FP)
	MOVD ZR, errno+16(FP)
	RET
mask_error:
	NEG R0, R0
	MOVD ZR, pid+8(FP)
	MOVD R0, errno+16(FP)
	RET
child:
	MOVD $154, R8 // setpgid(0,0)
	MOVD ZR, R0
	MOVD ZR, R1
	SVC
	CBNZ R0, fail
	MOVD ZR, R20
stdio:
	MOVD 40(R19), R0
	CMP $3, R20
	BLT duplicate
	MOVD 48(R19), R0
	CMP $3, R20
	BEQ duplicate
	MOVD 56(R19), R0
duplicate:
	MOVD $24, R8 // dup3 onto stdio or a barrier slot
	MOVD R20, R1
	MOVD ZR, R2
	SVC
	CMP $0, R0
	BLT fail
	ADD $1, R20
	CMP $5, R20
	BLT stdio
	MOVD $436, R8 // close_range(5, UINT_MAX, 0), independent of RLIMIT_NOFILE
	MOVD $5, R0
	MOVD $-1, R1
	MOVD ZR, R2
	SVC
	CBZ R0, setup
	MOVD $-38, R21 // ENOSYS: retain complete descriptor isolation on older kernels
	CMP R21, R0
	BNE fail
	MOVD $5, R20
close_fds:
	MOVD 64(R19), R21
	CMP R21, R20
	BGE setup
	MOVD $57, R8
	MOVD R20, R0
	SVC
	ADD $1, R20
	B close_fds
setup:
	MOVD $1, R4
	MOVB R4, 24(RSP)
	MOVD $64, R8
	MOVD $3, R0
	ADD $24, RSP, R1
	MOVD $1, R2
	SVC
	CMP $1, R0
	BNE fail
	MOVB ZR, 24(RSP)
	MOVD $63, R8
	MOVD $4, R0
	ADD $24, RSP, R1
	MOVD $1, R2
	SVC
	CMP $1, R0
	BNE fail
	MOVBU 24(RSP), R4
	CMP $1, R4
	BNE fail
	MOVD ZR, R20
	MOVD ZR, 32(RSP)
	MOVD ZR, 40(RSP)
	MOVD ZR, 48(RSP)
	MOVD ZR, 56(RSP)
reset_signals:
	MOVD 32(R19), R21
	CMP R21, R20
	BGE clear_mask
	MOVD 24(R19), R21
	MOVD (R21)(R20<<3), R0
	MOVD $134, R8
	ADD $32, RSP, R1
	MOVD ZR, R2
	MOVD $8, R3
	SVC
	CBNZ R0, fail
	ADD $1, R20
	B reset_signals
clear_mask:
	MOVD ZR, 64(RSP)
	MOVD $135, R8
	MOVD $2, R0
	ADD $64, RSP, R1
	MOVD ZR, R2
	MOVD $8, R3
	SVC
	CBNZ R0, fail
	MOVD $57, R8
	MOVD $3, R0
	SVC
	MOVD $57, R8
	MOVD $4, R0
	SVC
	MOVD $221, R8
	MOVD 0(R19), R0
	MOVD 8(R19), R1
	MOVD 16(R19), R2
	SVC
fail:
	MOVD $93, R8
	MOVD $127, R0
	SVC
	B fail
