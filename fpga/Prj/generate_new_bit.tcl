# Run separately only after reviewing STA for this exact new routed database.
# Input is the new routed database produced by this source build.
set project_dir [file dirname [file normalize [info script]]]
cd $project_dir
set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676
gen_bit_stream -create_bit_file TRUE -create_bin_file -persist_pin {Release For USER} -force_to_run -input_db_file [file join $project_dir {build_from_source/route/place_route/meeting_av_mini200_pnr.adf}]
