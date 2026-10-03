# Runs one Julia evaluator job inside the experiments-runner sandbox.
#
# runner.py starts this file once per job, as its own process in its own
# session, with rlimits and a scrubbed environment already applied:
#
#     julia --startup-file=no --history-file=no --heap-size-hint=1G harness.jl <job dir>
#
# The job directory holds `code` (the evaluator source) and `input.json` (the
# evaluator input contract). The harness includes the code into a fresh
# module, calls `evaluate(data)` with `data::Dict{String,Any}` and writes the
# envelope the runner reads back to `output.json`:
#
#     {"ok": true,  "result": {...}}
#     {"ok": false, "error_code": "exception" | "no_evaluate" | "not_dict"
#                                 | "not_serializable" | "bad_input"}
#
# The protocol needs no package: the small JSON reader and writer below cover
# exactly what the runner sends and accepts. Evaluators are free to use JSON3
# and the other packages of the image themselves; the protocol does not
# depend on any of them, so a broken or missing package can never break the
# harness. Stack traces go to stderr, which the runner captures into the
# job log.

module Harness

# Nesting limits: the input is whatever the Platform sends; the result must
# stay within what the runner accepts (128), as in the Python harness.
const MAX_DEPTH = 1000
const MAX_WRITE_DEPTH = 100
const PR_SET_DUMPABLE = Cint(4)

struct JSONError <: Exception
    msg::String
end
Base.showerror(io::IO, e::JSONError) = print(io, "JSON: ", e.msg)

struct NotSerializable <: Exception
    msg::String
end
Base.showerror(io::IO, e::NotSerializable) = print(io, "not serializable: ", e.msg)

# -- reader ------------------------------------------------------------------

mutable struct Reader
    buf::Vector{UInt8}
    pos::Int
end

@inline function peekbyte(r::Reader)::UInt8
    r.pos <= length(r.buf) || throw(JSONError("unexpected end of input"))
    return @inbounds r.buf[r.pos]
end

@inline function skipws!(r::Reader)
    buf = r.buf
    n = length(buf)
    pos = r.pos
    @inbounds while pos <= n
        b = buf[pos]
        (b == 0x20 || b == 0x09 || b == 0x0a || b == 0x0d) || break
        pos += 1
    end
    r.pos = pos
    return nothing
end

function expectbyte!(r::Reader, b::UInt8)
    peekbyte(r) == b || throw(JSONError("expected '$(Char(b))' at byte $(r.pos)"))
    r.pos += 1
    return nothing
end

function expectword!(r::Reader, word::String)
    for b in codeunits(word)
        expectbyte!(r, b)
    end
    return nothing
end

function parse_value(r::Reader, depth::Int)
    depth > MAX_DEPTH && throw(JSONError("nesting too deep"))
    skipws!(r)
    b = peekbyte(r)
    if b == UInt8('{')
        return parse_object(r, depth)
    elseif b == UInt8('[')
        return parse_array(r, depth)
    elseif b == UInt8('"')
        return parse_string(r)
    elseif b == UInt8('t')
        expectword!(r, "true")
        return true
    elseif b == UInt8('f')
        expectword!(r, "false")
        return false
    elseif b == UInt8('n')
        expectword!(r, "null")
        return nothing
    elseif b == UInt8('-') || (UInt8('0') <= b <= UInt8('9'))
        return parse_number(r)
    end
    throw(JSONError("unexpected character at byte $(r.pos)"))
end

function parse_object(r::Reader, depth::Int)
    r.pos += 1
    obj = Dict{String,Any}()
    skipws!(r)
    if peekbyte(r) == UInt8('}')
        r.pos += 1
        return obj
    end
    while true
        skipws!(r)
        peekbyte(r) == UInt8('"') || throw(JSONError("expected a key at byte $(r.pos)"))
        key = parse_string(r)
        skipws!(r)
        expectbyte!(r, UInt8(':'))
        obj[key] = parse_value(r, depth + 1)
        skipws!(r)
        b = peekbyte(r)
        r.pos += 1
        b == UInt8('}') && return obj
        b == UInt8(',') || throw(JSONError("expected ',' or '}' at byte $(r.pos - 1)"))
    end
end

function parse_array(r::Reader, depth::Int)
    r.pos += 1
    arr = Any[]
    skipws!(r)
    if peekbyte(r) == UInt8(']')
        r.pos += 1
        return arr
    end
    while true
        push!(arr, parse_value(r, depth + 1))
        skipws!(r)
        b = peekbyte(r)
        r.pos += 1
        b == UInt8(']') && return arr
        b == UInt8(',') || throw(JSONError("expected ',' or ']' at byte $(r.pos - 1)"))
    end
end

function hexdigit(b::UInt8)::UInt32
    UInt8('0') <= b <= UInt8('9') && return UInt32(b - UInt8('0'))
    UInt8('a') <= b <= UInt8('f') && return UInt32(b - UInt8('a') + 0x0a)
    UInt8('A') <= b <= UInt8('F') && return UInt32(b - UInt8('A') + 0x0a)
    throw(JSONError("bad \\u escape"))
end

function read_hex4!(r::Reader)::UInt32
    r.pos + 3 <= length(r.buf) || throw(JSONError("unexpected end of input"))
    v = UInt32(0)
    for i in 0:3
        v = (v << 4) | hexdigit(r.buf[r.pos + i])
    end
    r.pos += 4
    return v
end

function parse_string(r::Reader)::String
    buf = r.buf
    n = length(buf)
    r.pos += 1                        # opening quote
    start = r.pos
    # Fast path: no escapes.
    pos = start
    @inbounds while pos <= n
        b = buf[pos]
        if b == UInt8('"')
            r.pos = pos + 1
            return String(buf[start:pos-1])
        elseif b == UInt8('\\')
            break
        elseif b < 0x20
            throw(JSONError("control character in string at byte $pos"))
        end
        pos += 1
    end
    pos > n && throw(JSONError("unterminated string"))
    io = IOBuffer()
    write(io, view(buf, start:pos-1))
    r.pos = pos
    while true
        r.pos <= n || throw(JSONError("unterminated string"))
        b = buf[r.pos]
        if b == UInt8('"')
            r.pos += 1
            return String(take!(io))
        elseif b == UInt8('\\')
            r.pos += 1
            r.pos <= n || throw(JSONError("unterminated string"))
            e = buf[r.pos]
            r.pos += 1
            if e == UInt8('"') || e == UInt8('\\') || e == UInt8('/')
                write(io, e)
            elseif e == UInt8('b')
                write(io, UInt8('\b'))
            elseif e == UInt8('f')
                write(io, UInt8('\f'))
            elseif e == UInt8('n')
                write(io, UInt8('\n'))
            elseif e == UInt8('r')
                write(io, UInt8('\r'))
            elseif e == UInt8('t')
                write(io, UInt8('\t'))
            elseif e == UInt8('u')
                cp = read_hex4!(r)
                if 0xd800 <= cp <= 0xdbff
                    # A high surrogate needs its low half; alone it is U+FFFD.
                    if r.pos + 5 <= n && buf[r.pos] == UInt8('\\') && buf[r.pos + 1] == UInt8('u')
                        save = r.pos
                        r.pos += 2
                        lo = read_hex4!(r)
                        if 0xdc00 <= lo <= 0xdfff
                            cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00)
                        else
                            r.pos = save
                            cp = 0xfffd
                        end
                    else
                        cp = 0xfffd
                    end
                elseif 0xdc00 <= cp <= 0xdfff
                    cp = 0xfffd
                end
                print(io, Char(cp))
            else
                throw(JSONError("bad escape at byte $(r.pos - 1)"))
            end
        elseif b < 0x20
            throw(JSONError("control character in string at byte $(r.pos)"))
        else
            write(io, b)
            r.pos += 1
        end
    end
end

function parse_number(r::Reader)
    buf = r.buf
    n = length(buf)
    start = r.pos
    pos = start
    isfloat = false
    pos <= n && buf[pos] == UInt8('-') && (pos += 1)
    pos <= n || throw(JSONError("bad number"))
    if buf[pos] == UInt8('0')
        pos += 1
    elseif UInt8('1') <= buf[pos] <= UInt8('9')
        while pos <= n && UInt8('0') <= buf[pos] <= UInt8('9')
            pos += 1
        end
    else
        throw(JSONError("bad number at byte $start"))
    end
    if pos <= n && buf[pos] == UInt8('.')
        isfloat = true
        pos += 1
        (pos <= n && UInt8('0') <= buf[pos] <= UInt8('9')) || throw(JSONError("bad number at byte $start"))
        while pos <= n && UInt8('0') <= buf[pos] <= UInt8('9')
            pos += 1
        end
    end
    if pos <= n && (buf[pos] == UInt8('e') || buf[pos] == UInt8('E'))
        isfloat = true
        pos += 1
        pos <= n && (buf[pos] == UInt8('+') || buf[pos] == UInt8('-')) && (pos += 1)
        (pos <= n && UInt8('0') <= buf[pos] <= UInt8('9')) || throw(JSONError("bad number at byte $start"))
        while pos <= n && UInt8('0') <= buf[pos] <= UInt8('9')
            pos += 1
        end
    end
    text = String(buf[start:pos-1])
    r.pos = pos
    if !isfloat
        v = tryparse(Int64, text)
        v === nothing || return v
    end
    return parse(Float64, text)
end

"""Parse a complete JSON document; trailing content other than whitespace is an error."""
function parse_json(bytes::Vector{UInt8})
    r = Reader(bytes, 1)
    v = parse_value(r, 0)
    skipws!(r)
    r.pos <= length(r.buf) && throw(JSONError("trailing content at byte $(r.pos)"))
    return v
end

# -- writer ------------------------------------------------------------------

function write_string(io::IO, s::AbstractString)
    write(io, UInt8('"'))
    for c in s
        if !isvalid(c)
            write(io, "\\ufffd")            # invalid UTF-8 in the result
        elseif c == '"'
            write(io, "\\\"")
        elseif c == '\\'
            write(io, "\\\\")
        elseif c == '\n'
            write(io, "\\n")
        elseif c == '\r'
            write(io, "\\r")
        elseif c == '\t'
            write(io, "\\t")
        elseif c < ' '
            write(io, "\\u", string(UInt32(c), base=16, pad=4))
        else
            print(io, c)
        end
    end
    write(io, UInt8('"'))
    return nothing
end

function write_float(io::IO, x::Float64)
    if isfinite(x)
        # Julia prints the shortest round-trip form ("0.1", "1.0e-7"), which
        # is valid JSON as it stands.
        print(io, x)
    else
        write(io, "null")
    end
    return nothing
end

function write_array_dims(io::IO, a::AbstractArray, depth::Int)
    # Nested lists, first index outermost -- the shape numpy's tolist() gives.
    if ndims(a) <= 1
        write(io, UInt8('['))
        first = true
        for v in a
            first || write(io, UInt8(','))
            first = false
            write_json(io, v, depth + 1)
        end
        write(io, UInt8(']'))
    else
        write(io, UInt8('['))
        first = true
        for s in eachslice(a; dims=1)
            first || write(io, UInt8(','))
            first = false
            write_array_dims(io, s, depth + 1)
        end
        write(io, UInt8(']'))
    end
    return nothing
end

function write_pairs(io::IO, kvs, depth::Int)
    write(io, UInt8('{'))
    first = true
    for (k, v) in kvs
        first || write(io, UInt8(','))
        first = false
        write_string(io, k isa AbstractString ? k : string(k))
        write(io, UInt8(':'))
        write_json(io, v, depth + 1)
    end
    write(io, UInt8('}'))
    return nothing
end

"""Write `x` as JSON. NaN and Inf become null; unknown types are refused."""
function write_json(io::IO, x, depth::Int=0)
    depth > MAX_WRITE_DEPTH && throw(NotSerializable("nesting deeper than $MAX_WRITE_DEPTH levels"))
    if x === nothing || x === missing
        write(io, "null")
    elseif x isa Bool
        write(io, x ? "true" : "false")
    elseif x isa Integer
        print(io, string(x))
    elseif x isa AbstractFloat
        write_float(io, Float64(x))
    elseif x isa Real
        write_float(io, Float64(x))         # Rational, Irrational
    elseif x isa AbstractString || x isa Symbol || x isa AbstractChar
        write_string(io, string(x))
    elseif x isa AbstractDict
        write_pairs(io, x, depth)
    elseif x isa NamedTuple
        write_pairs(io, pairs(x), depth)
    elseif x isa AbstractArray
        write_array_dims(io, x, depth)
    elseif x isa Tuple || x isa AbstractSet
        write(io, UInt8('['))
        first = true
        for v in x
            first || write(io, UInt8(','))
            first = false
            write_json(io, v, depth + 1)
        end
        write(io, UInt8(']'))
    else
        throw(NotSerializable("value of type $(typeof(x))"))
    end
    return nothing
end

# -- job ---------------------------------------------------------------------

function harden()
    # Every job runs under the same uid; a non-dumpable process cannot be
    # ptraced or read through /proc/<pid>/mem by a concurrent job.
    try
        ccall(:prctl, Cint, (Cint, Culong, Culong, Culong, Culong), PR_SET_DUMPABLE, 0, 0, 0, 0)
    catch
    end
    return nothing
end

function write_envelope(jobdir::String, envelope_json::String)
    tmp = joinpath(jobdir, "output.json.tmp")
    open(tmp, "w") do io
        write(io, envelope_json)
    end
    mv(tmp, joinpath(jobdir, "output.json"); force=true)
    return nothing
end

failure(code::String) = "{\"ok\":false,\"error_code\":\"$code\"}"

function report(e, bt)
    try
        showerror(stderr, e, bt)
        println(stderr)
    catch
        println(stderr, "(the error could not be printed)")
    end
    return nothing
end

function run_job(code::String, data::Dict{String,Any})::String
    mod = Module(:Evaluator)
    try
        Base.include_string(mod, code, "evaluator.jl")
    catch e
        report(e, catch_backtrace())
        return failure("exception")
    end
    if !isdefined(mod, :evaluate)
        println(stderr, "evaluator.jl defines no function evaluate(data).")
        return failure("no_evaluate")
    end
    result = try
        Base.invokelatest(getfield(mod, :evaluate), data)
    catch e
        report(e, catch_backtrace())
        return failure("exception")
    end
    if !(result isa AbstractDict)
        println(stderr, "evaluate(data) returned $(typeof(result)), not a Dict.")
        return failure("not_dict")
    end
    io = IOBuffer()
    try
        write(io, "{\"ok\":true,\"result\":")
        write_json(io, result, 0)
        write(io, "}")
    catch e
        report(e, catch_backtrace())
        return failure("not_serializable")
    end
    return String(take!(io))
end

function main(args::Vector{String})::Cint
    if length(args) != 1
        println(stderr, "usage: harness.jl <job dir>")
        return Cint(2)
    end
    jobdir = args[1]
    harden()
    code_path = joinpath(jobdir, "code")
    input_path = joinpath(jobdir, "input.json")
    envelope = try
        code = read(code_path, String)
        data = parse_json(read(input_path))
        data isa Dict{String,Any} || throw(JSONError("input is not an object"))
        # Gone before any user code runs: another job under the same uid
        # finds nothing to read here.
        rm(code_path; force=true)
        rm(input_path; force=true)
        run_job(code, data)
    catch e
        report(e, catch_backtrace())
        failure("bad_input")
    end
    status = Cint(startswith(envelope, "{\"ok\":true,") ? 0 : 1)
    try
        write_envelope(jobdir, envelope)
    catch e
        report(e, catch_backtrace())
        status = Cint(1)
    end
    return status
end

end # module Harness

let status = Harness.main(ARGS)
    flush(stdout)
    flush(stderr)
    # _exit: tasks and atexit hooks the user code left behind must neither
    # keep the job alive nor change its result.
    ccall(:_exit, Cvoid, (Cint,), status)
end
