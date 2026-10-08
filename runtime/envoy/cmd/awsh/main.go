//go:build linux

package main

import (
	"os"

	"github.com/omry/omegaflow/runtime/envoy/awsh/helper"
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "bash-fail-stop" {
		helper.FailStop()
	}
	if len(os.Args) >= 2 && os.Args[1] == "bash-helper" {
		if helper.StartupCommand(os.Args[2:]) == nil {
			return
		}
	}
	os.Exit(2)
}
