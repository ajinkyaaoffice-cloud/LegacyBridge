       IDENTIFICATION DIVISION.
       PROGRAM-ID. AMORT.
      *================================================================*
      * AMORT - LEVEL PAYMENT FOR A FIXED RATE LOAN                    *
      *   PAYMENT = P * R * (1+R)**N / ((1+R)**N - 1),  R = RATE / 12  *
      * (1+R)**N IS BUILT BY REPEATED MULTIPLICATION IN A 9-DECIMAL    *
      * FIELD, EXACTLY AS THE 1987 MAINFRAME VERSION DID IT.           *
      *================================================================*
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-MRATE                PIC 9V9(9) COMP-3.
       01  WS-FACTOR               PIC 9(5)V9(9) COMP-3.
       01  WS-I                    PIC 9(3) COMP-3.
       01  WS-TOTAL                PIC 9(9)V99 COMP-3.
       LINKAGE SECTION.
       COPY LOANPARM.
       PROCEDURE DIVISION USING LOAN-PARMS.
       0000-MAIN.
           MOVE 0 TO LP-RETURN-CODE
           IF LP-PRINCIPAL = 0 OR LP-TERM-MONTHS = 0
              OR LP-TERM-MONTHS > 480 OR LP-ANNUAL-RATE > 0.5
               MOVE 10 TO LP-RETURN-CODE
               MOVE 0 TO LP-PAYMENT LP-TOTAL-INTEREST
               GOBACK
           END-IF
           IF LP-ANNUAL-RATE = 0
               COMPUTE LP-PAYMENT ROUNDED =
                   LP-PRINCIPAL / LP-TERM-MONTHS
           ELSE
               COMPUTE WS-MRATE = LP-ANNUAL-RATE / 12
               MOVE 1 TO WS-FACTOR
               PERFORM VARYING WS-I FROM 1 BY 1
                       UNTIL WS-I > LP-TERM-MONTHS
                   COMPUTE WS-FACTOR = WS-FACTOR * (1 + WS-MRATE)
               END-PERFORM
               COMPUTE LP-PAYMENT ROUNDED = LP-PRINCIPAL * WS-MRATE
                   * WS-FACTOR / (WS-FACTOR - 1)
                   ON SIZE ERROR
                       MOVE 30 TO LP-RETURN-CODE
                       MOVE 0 TO LP-PAYMENT
               END-COMPUTE
           END-IF
           COMPUTE WS-TOTAL = LP-PAYMENT * LP-TERM-MONTHS
           IF WS-TOTAL > LP-PRINCIPAL
               COMPUTE LP-TOTAL-INTEREST = WS-TOTAL - LP-PRINCIPAL
           ELSE
               MOVE 0 TO LP-TOTAL-INTEREST
           END-IF
           GOBACK.
