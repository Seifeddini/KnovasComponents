# Stand-in for HypothesisTests.jl, used only by test/runtests.jl to run the
# Julia evaluator example of docs/features/experiments.md: it records the
# samples the example compares; the statistics are not what is tested.
module HypothesisTests

export UnequalVarianceTTest, confint, pvalue

const CALLS = Vector{Tuple{Vector{Float64},Vector{Float64}}}()

struct UnequalVarianceTTest
    x::Vector{Float64}
    y::Vector{Float64}
    function UnequalVarianceTTest(x, y)
        push!(CALLS, (collect(Float64, x), collect(Float64, y)))
        return new(collect(Float64, x), collect(Float64, y))
    end
end

confint(::UnequalVarianceTTest) = (-1.0, 1.0)
pvalue(::UnequalVarianceTTest) = 0.5

end
