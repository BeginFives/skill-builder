// Eval fixture — a change to REVIEW. It contains multiple rubric violations the
// golang-engineer skill should catch. Not part of any module.
package worker

import (
	"log"
	"net/http"
)

type Job struct{ ID string }

// StartBackground kicks off processing for a batch of jobs.
func StartBackground(jobs []Job) {
	for _, j := range jobs {
		go process(j) // violation: fire-and-forget — no owner, no ctx, no stop condition
	}
}

func process(j Job) {
	res, _ := doWork(j)         // violation: swallowed error
	log.Println("done", res.ID) // violation: unstructured logging, no correlation
}

func doWork(j Job) (Job, error) { return j, nil }

// Serve starts the HTTP server.
func Serve(mux *http.ServeMux) {
	srv := &http.Server{Addr: ":8080", Handler: mux} // violation: zero timeouts (Slowloris DoS)
	srv.ListenAndServe()                             // violation: error ignored, no graceful shutdown
}
