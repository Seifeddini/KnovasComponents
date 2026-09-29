# Tests of KnovasExperiments.jl that need neither a Platform nor a network:
#
#   julia --startup-file=no KnovasPlatform/experiments-sdk/julia/test/runtests.jl
#
# With HTTP.jl and JSON3.jl installed (CI installs them) the client loads with
# them; without, the stand-ins in test/stubs let it load, since the code under
# test does not talk HTTP. HypothesisTests is always the stand-in unless
# installed: it only records what the documentation's example compares.

using Test

const HERE = @__DIR__
# Last on the load path: a package that is installed is found first.
push!(LOAD_PATH, joinpath(HERE, "stubs"))
include(joinpath(HERE, "..", "KnovasExperiments.jl"))
using .KnovasExperiments
const KX = KnovasExperiments

ev(id, status; verdict=nothing, name="builtin.paired_t") = Dict{String,Any}(
    "id" => id, "status" => status, "verdict" => verdict, "evaluator_name" => name)

# The CI gate the README shows.
gate_fails(evs) = any(e -> get(e, "verdict", nothing) == "worse" ||
                           get(e, "status", nothing) == "failed", evs)

has_error(x) = x isa Expr && (x.head in (:error, :incomplete) || any(has_error, x.args))

@testset "KnovasExperiments" begin
    @testset "wait_for is exported" begin
        @test :wait_for in names(KnovasExperiments)
        @test wait_for === KX.wait_for
    end

    @testset "nothing pending: returns at once" begin
        polls = Ref(0)
        evs = Any[ev("a", "done"; verdict="better"),
                  Dict{String,Any}("evaluator_name" => "builtin.welch_t", "status" => "skipped")]
        out = KX._wait_for(() -> (polls[] += 1; Any[]), evs; pause=_ -> error("no pause expected"))
        @test polls[] == 0
        @test out == evs
    end

    @testset "polls until done and returns only this push's evaluations" begin
        # e2e-api-2: the history (evaluations()) still holds the 'worse' of an
        # earlier push after the regression was fixed.
        old = ev("old", "done"; verdict="worse")
        pushed = Any[ev("p1", "done"; verdict="better"),
                     ev("p2", "queued"; name="example.bootstrap_mean_py"),
                     Dict{String,Any}("evaluator_name" => "builtin.welch_t", "status" => "skipped")]
        history = [Any[ev("p2", "running"), old],
                   Any[ev("p2", "done"; verdict="inconclusive"), ev("p1", "done"; verdict="better"), old]]
        polls = Ref(0)
        pauses = Float64[]
        out = KX._wait_for(() -> history[polls[] += 1], pushed; interval=3,
                           pause=t -> push!(pauses, t))
        @test polls[] == 2
        @test pauses == [3.0, 3.0]
        @test [get(e, "id", nothing) for e in out] == ["p1", "p2", nothing]
        @test out[2]["status"] == "done"
        @test out[2]["verdict"] == "inconclusive"
        @test out[3]["status"] == "skipped"
        @test !gate_fails(out)
        # The gate the README used before, over the history, stayed red.
        @test gate_fails(history[end])
    end

    @testset "a queued evaluator that fails fails the gate" begin
        out = KX._wait_for(() -> Any[ev("p", "failed")], Any[ev("p", "queued")]; pause=_ -> nothing)
        @test gate_fails(out)
    end

    @testset "gives up after the timeout" begin
        now_ = Ref(0.0)
        err = try
            KX._wait_for(() -> Any[ev("p", "running")], Any[ev("p", "queued")];
                         timeout=10, interval=4, pause=t -> (now_[] += t), clock=() -> now_[])
            nothing
        catch e
            e
        end
        @test err isa ExperimentsError
        @test err.kind == :timeout
        @test occursin("1 Auswertung(en) nach 10 s", err.message)
        @test now_[] == 12.0
        @test_throws ExperimentsError KX._wait_for(() -> Any[], Any[]; timeout=Inf)
    end

    @testset "the documented Welch evaluator counts each answer of a scale" begin
        # review-stats-2: an ordinal row is a level with count answers on it,
        # not a sum; the example must compare the answers, not value / count.
        docs = read(joinpath(HERE, "..", "..", "..", "docs", "features", "experiments.md"), String)
        block = only(m.captures[1] for m in eachmatch(r"```julia\n(.*?)```"s, docs)
                     if occursin("UnequalVarianceTTest", m.captures[1]))
        example = Module(:WelchExample)
        Base.include_string(example, block)
        HT = example.HypothesisTests
        rows(kind_rows) = [Dict{String,Any}("variant" => v, "value" => x, "count" => c)
                           for (v, x, c) in kind_rows]
        data(kind, kind_rows) = Dict{String,Any}(
            "metric" => Dict{String,Any}("kind" => kind, "direction" => "higher", "unit" => ""),
            "variants" => Any[Dict{String,Any}("key" => "A", "is_control" => true),
                              Dict{String,Any}("key" => "B", "is_control" => false)],
            "rows" => rows(kind_rows))
        empty!(HT.CALLS)
        example.evaluate(data("ordinal", [("A", 4, 3), ("A", 5, 1), ("B", 5, 2)]))
        @test HT.CALLS == [([5.0, 5.0], [4.0, 4.0, 4.0, 5.0])]
        empty!(HT.CALLS)
        example.evaluate(data("mean", [("A", 6.0, 3), ("A", 1.0, 1), ("B", 4.0, 2), ("B", 2.0, 1)]))
        @test HT.CALLS == [([2.0, 2.0], [2.0, 1.0])]
    end

    @testset "code examples parse" begin
        for file in (joinpath(HERE, "..", "README.md"),
                     joinpath(HERE, "..", "..", "..", "docs", "features", "experiments.md"))
            blocks = [m.captures[1] for m in eachmatch(r"```julia\n(.*?)```"s, read(file, String))]
            @test !isempty(blocks)
            for block in blocks
                @test !has_error(Meta.parseall(block))
            end
        end
    end
end
