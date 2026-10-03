# Stand-in for HTTP.jl, used only by test/runtests.jl when HTTP.jl is not
# installed: KnovasExperiments names nothing of HTTP at load time, and the
# tested code does not talk HTTP.
module HTTP
end
