// Package harness anchors the external deps the A/B graders may import in candidate code,
// so `go mod tidy` keeps them and go.sum stays complete for readonly builds.
package harness

import (
	_ "github.com/google/uuid"
	_ "github.com/stretchr/testify/assert"
	_ "github.com/stretchr/testify/require"
	_ "go.opentelemetry.io/otel"
	_ "go.opentelemetry.io/otel/attribute"
	_ "go.opentelemetry.io/otel/codes"
	_ "go.opentelemetry.io/otel/metric"
	_ "go.opentelemetry.io/otel/trace"
	_ "golang.org/x/sync/errgroup"
)
