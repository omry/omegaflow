//go:build linux

package main

import (
	"os"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
)

func main() {
	if len(os.Args) != 2 || os.Args[1] != "bash-fail-stop" {
		os.Exit(2)
	}
	helper.FailStop()
}
