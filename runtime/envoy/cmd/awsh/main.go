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
		if len(os.Args) >= 4 && os.Args[3] == "start-prepared" {
			if helper.PreparedCommand(os.Args[2:], os.Stdout) == nil {
				return
			}
			os.Exit(2)
		}
		if len(os.Args) >= 4 && os.Args[3] == "source" {
			if helper.SourceCommand(os.Args[2:], os.Stdout) == nil {
				return
			}
			os.Exit(2)
		}
		if helper.StartupCommand(os.Args[2:]) == nil {
			return
		}
	}
	os.Exit(2)
}
