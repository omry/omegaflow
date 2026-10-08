#include "textflag.h"

// Everything between clone and exec/exit is raw, async-signal-safe child code.
TEXT ·forkChild(SB),NOSPLIT,$80-24
	MOVQ args+0(FP), R12
	MOVQ $-1, 0(SP)
	MOVQ $14, AX // rt_sigprocmask: block signals on this thread before fork
	MOVQ $2, DI
	LEAQ 0(SP), SI
	LEAQ 8(SP), DX
	MOVQ $8, R10
	SYSCALL
	CMPQ AX, $0
	JL mask_error
	MOVQ $56, AX // clone(SIGCHLD), separate address space
	MOVQ $17, DI
	XORQ SI, SI
	XORQ DX, DX
	XORQ R10, R10
	XORQ R8, R8
	SYSCALL
	CMPQ AX, $0
	JE child
	MOVQ AX, R13
	MOVQ $14, AX
	MOVQ $2, DI
	LEAQ 8(SP), SI
	XORQ DX, DX
	MOVQ $8, R10
	SYSCALL
	MOVQ R13, AX
	CMPQ AX, $0
	JL mask_error
	MOVQ AX, pid+8(FP)
	MOVQ $0, errno+16(FP)
	RET
mask_error:
	NEGQ AX
	MOVQ $0, pid+8(FP)
	MOVQ AX, errno+16(FP)
	RET
child:
	MOVQ $109, AX // setpgid(0,0)
	XORQ DI, DI
	XORQ SI, SI
	SYSCALL
	TESTQ AX, AX
	JNZ fail
	XORQ R13, R13
stdio:
	MOVQ 40(R12), DI
	CMPQ R13, $3
	JL duplicate
	MOVQ 48(R12), DI
	CMPQ R13, $3
	JE duplicate
	MOVQ 56(R12), DI
duplicate:
	MOVQ $292, AX // dup3 onto stdio or a barrier slot
	MOVQ R13, SI
	XORQ DX, DX
	SYSCALL
	CMPQ AX, $0
	JL fail
	INCQ R13
	CMPQ R13, $5
	JL stdio
	MOVQ $436, AX // close_range(5, UINT_MAX, 0), independent of RLIMIT_NOFILE
	MOVQ $5, DI
	MOVQ $-1, SI
	XORQ DX, DX
	SYSCALL
	TESTQ AX, AX
	JZ setup
	CMPQ AX, $-38 // ENOSYS: retain complete descriptor isolation on older kernels
	JNE fail
	MOVQ $5, R13
close_fds:
	CMPQ R13, 64(R12)
	JGE setup
	MOVQ $3, AX
	MOVQ R13, DI
	SYSCALL
	INCQ R13
	JMP close_fds
setup:
	MOVB $1, 16(SP)
	MOVQ $1, AX
	MOVQ $3, DI
	LEAQ 16(SP), SI
	MOVQ $1, DX
	SYSCALL
	CMPQ AX, $1
	JNE fail
	MOVB $0, 16(SP)
	XORQ AX, AX
	MOVQ $4, DI
	LEAQ 16(SP), SI
	MOVQ $1, DX
	SYSCALL
	CMPQ AX, $1
	JNE fail
	CMPB 16(SP), $1
	JNE fail
	XORQ R13, R13
	MOVQ $0, 24(SP)
	MOVQ $0, 32(SP)
	MOVQ $0, 40(SP)
	MOVQ $0, 48(SP)
reset_signals:
	CMPQ R13, 32(R12)
	JGE clear_mask
	MOVQ 24(R12), R15
	MOVQ (R15)(R13*8), DI
	MOVQ $13, AX
	LEAQ 24(SP), SI
	XORQ DX, DX
	MOVQ $8, R10
	SYSCALL
	TESTQ AX, AX
	JNZ fail
	INCQ R13
	JMP reset_signals
clear_mask:
	MOVQ $0, 56(SP)
	MOVQ $14, AX
	MOVQ $2, DI
	LEAQ 56(SP), SI
	XORQ DX, DX
	MOVQ $8, R10
	SYSCALL
	TESTQ AX, AX
	JNZ fail
	MOVQ $3, AX
	MOVQ $3, DI
	SYSCALL
	MOVQ $3, AX
	MOVQ $4, DI
	SYSCALL
	MOVQ $59, AX
	MOVQ 0(R12), DI
	MOVQ 8(R12), SI
	MOVQ 16(R12), DX
	SYSCALL
fail:
	MOVQ $60, AX
	MOVQ $127, DI
	SYSCALL
	JMP fail
