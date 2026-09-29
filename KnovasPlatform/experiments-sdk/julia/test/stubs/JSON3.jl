# Stand-in for JSON3.jl, used only by test/runtests.jl when JSON3.jl is not
# installed: the two types KnovasExperiments dispatches on at load time.
module JSON3
struct Object end
struct Array end
end
